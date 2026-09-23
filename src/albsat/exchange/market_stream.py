"""Binance Spot WebSocket piyasa akışı (SPEC.md §2 "Bağlantı yöntemi").

Tek bir bağlantı, birleşik akış adresiyle (``/stream?streams=a/b/c``) bütün
coinlerin 1m/15m/1h mumlarını, en iyi alış-satış fiyatını (``bookTicker``)
ve 24 saatlik özetini (``miniTicker``) taşır. Kurallar
``web-socket-streams.md``'den:

* Adres ``wss://stream.binance.com:443`` (ya da ``:9443``); semboller küçük harf.
* Bir bağlantı 24 saat geçerlidir; süre dolmadan kendimiz yeniden bağlanırız.
* Sunucu 20 saniyede bir ping gönderir, 60 saniye içinde pong gelmezse
  bağlantıyı keser. ``websockets`` kütüphanesi pong'u kendisi gönderir.
* İstemciden gelen mesaj sınırı saniyede 5 (ping/pong dahil). Bu modül
  abone olmak için mesaj göndermez; akışlar adreste yazılıdır.
* IP başına 5 dakikada 300 bağlantı denemesi. Yeniden bağlanma üstel
  beklemeyle yapılır ve 5 dakikada 20 denemeyi geçmez.

Bu modül yalnızca piyasa verisi okur; API anahtarı kullanmaz, emir
göndermez.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from albsat.data.klines import interval_ms
from albsat.paper.fills import Candle

#: 24 saatlik sınırdan önce kendiliğinden yenileme.
RENEW_AFTER_SECONDS = 23 * 3600 + 30 * 60
#: Bu kadar süre hiç mesaj gelmezse bağlantı ölü sayılır.
SILENCE_SECONDS = 60.0
MAX_ATTEMPTS_PER_5_MIN = 20


@dataclass(frozen=True)
class KlineEvent:
    sembol: str
    periyot: str
    mum: Candle
    kapandi: bool
    #: Borsanın ham satırı (``KlineStore``'a yazmak için, REST biçiminde).
    ham: tuple[Any, ...]


@dataclass(frozen=True)
class BookEvent:
    sembol: str
    alis: Decimal
    satis: Decimal


@dataclass(frozen=True)
class TickerEvent:
    sembol: str
    son_fiyat: Decimal
    hacim_quote_24s: Decimal


Event = KlineEvent | BookEvent | TickerEvent


def stream_names(
    symbols: Sequence[str], intervals: Sequence[str], *, extra_tickers: Sequence[str] = ()
) -> list[str]:
    names: list[str] = []
    for symbol in symbols:
        lower = symbol.lower()
        names.extend(f"{lower}@kline_{interval}" for interval in intervals)
        names.append(f"{lower}@bookTicker")
        names.append(f"{lower}@miniTicker")
    names.extend(f"{symbol.lower()}@miniTicker" for symbol in extra_tickers)
    return names


def stream_url(base: str, names: Sequence[str]) -> str:
    """``base`` birleşik akış adresi, ör. ``wss://stream.binance.com/stream``."""
    return f"{base.rstrip('/')}?streams={'/'.join(names)}"


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Sayı okunamadı: {value!r}") from error


def parse_message(text: str | bytes) -> Event | None:
    """Birleşik akış mesajını olaya çevirir; tanınmayan mesajda ``None``."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    try:
        if data.get("e") == "kline":
            k = data["k"]
            interval = str(k["i"])
            row = (
                int(k["t"]), str(k["o"]), str(k["h"]), str(k["l"]), str(k["c"]),
                str(k["v"]), int(k["T"]), str(k["q"]), int(k["n"]), str(k["V"]),
                str(k["Q"]), "0",
            )
            return KlineEvent(
                sembol=str(k["s"]).upper(),
                periyot=interval,
                mum=Candle.from_rest(list(row), interval_ms(interval)),
                kapandi=bool(k["x"]),
                ham=row,
            )
        if data.get("e") == "24hrMiniTicker":
            return TickerEvent(
                sembol=str(data["s"]).upper(),
                son_fiyat=_dec(data["c"]),
                hacim_quote_24s=_dec(data["q"]),
            )
        if {"u", "s", "b", "a"} <= data.keys():
            return BookEvent(
                sembol=str(data["s"]).upper(), alis=_dec(data["b"]), satis=_dec(data["a"])
            )
    except (KeyError, ValueError, TypeError):
        return None
    return None


class Connection(Protocol):
    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


Connector = Callable[[str], Connection]


def default_connector(url: str) -> Connection:
    """``websockets`` ile bağlanır. TLS doğrulaması açıktır; güven deposu
    ``core.tls.enable_system_trust`` ile işletim sisteminindir."""
    from websockets.sync.client import connect

    return connect(url, open_timeout=15, ping_interval=20, ping_timeout=20,
                   close_timeout=5, max_size=2**20)


@dataclass(frozen=True)
class StreamStatus:
    bagli: bool
    bagli_oldugu_an: float | None
    son_mesaj_saniye_once: float | None
    yeniden_baglanma: int
    son_hata: str | None
    mesaj_sayisi: int


class MarketStream:
    """Arka planda bağlı kalan, koptuğunda yeniden bağlanan akış okuyucusu."""

    def __init__(
        self,
        url: str,
        *,
        on_event: Callable[[Event], None],
        on_state: Callable[[bool, str], None] | None = None,
        connector: Connector = default_connector,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.on_event = on_event
        self.on_state = on_state
        self.connector = connector
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: Connection | None = None
        self._lock = threading.Lock()
        self._connected_at: float | None = None
        self._last_message: float | None = None
        self._reconnects = 0
        self._messages = 0
        self._last_error: str | None = None
        self._attempts: deque[float] = deque()
        self._force = threading.Event()

    # --- yaşam döngüsü ------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="piyasa-akisi", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._close()
        if self._thread is not None:
            self._thread.join(timeout)

    def reconnect(self) -> None:
        """Bağlantıyı kapatıp yeniden açtırır (uyanma sonrası, sessizlikte)."""
        self._force.set()
        self._close()

    def _close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()

    def status(self) -> StreamStatus:
        with self._lock:
            now = self.clock()
            return StreamStatus(
                bagli=self._connection is not None and self._connected_at is not None,
                bagli_oldugu_an=self._connected_at,
                son_mesaj_saniye_once=None if self._last_message is None
                else now - self._last_message,
                yeniden_baglanma=self._reconnects,
                son_hata=self._last_error,
                mesaj_sayisi=self._messages,
            )

    # --- iç döngü -----------------------------------------------------

    def _wait_before_attempt(self, failures: int) -> bool:
        """Üstel bekleme ve 5 dakikalık deneme sınırı. Durdurulduysa ``False``."""
        now = self.clock()
        while self._attempts and now - self._attempts[0] > 300:
            self._attempts.popleft()
        delay = min(60.0, 2.0 ** min(failures, 6)) if failures else 0.0
        if len(self._attempts) >= MAX_ATTEMPTS_PER_5_MIN:
            delay = max(delay, 300 - (now - self._attempts[0]))
        if delay > 0 and self._stop.wait(delay):
            return False
        self._attempts.append(self.clock())
        return not self._stop.is_set()

    def _set_state(self, connected: bool, reason: str) -> None:
        if self.on_state is not None:
            # Durum bildirimindeki bir hata akışı durdurmasın.
            with contextlib.suppress(Exception):
                self.on_state(connected, reason)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if not self._wait_before_attempt(failures):
                break
            try:
                connection = self.connector(self.url)
            except Exception as error:  # noqa: BLE001 - her bağlantı hatası yeniden denenir
                failures += 1
                self._last_error = f"Bağlanılamadı: {type(error).__name__}: {error}"[:300]
                self._set_state(False, self._last_error)
                continue
            with self._lock:
                self._connection = connection
                self._connected_at = self.clock()
                self._last_message = self.clock()
            self._force.clear()
            if failures or self._reconnects:
                self._reconnects += 1
            failures = 0
            self._set_state(True, "bağlandı")
            reason = self._pump(connection)
            with self._lock:
                self._connection = None
                self._connected_at = None
            self._close_quietly(connection)
            if self._stop.is_set():
                break
            self._last_error = reason
            self._set_state(False, reason)
            if not self._force.is_set():
                failures = max(failures, 1)

    def _pump(self, connection: Connection) -> str:
        started = self.clock()
        while not self._stop.is_set():
            if self._force.is_set():
                return "yeniden bağlanma istendi"
            if self.clock() - started > RENEW_AFTER_SECONDS:
                self._force.set()
                return "24 saatlik bağlantı süresi doluyor; yenileniyor"
            try:
                message = connection.recv(timeout=5.0)
            except TimeoutError:
                last = self._last_message or started
                if self.clock() - last > SILENCE_SECONDS:
                    return f"{SILENCE_SECONDS:.0f} saniyedir veri gelmiyor"
                continue
            except Exception as error:  # noqa: BLE001 - kopma yeniden bağlanmayla çözülür
                return f"Bağlantı koptu: {type(error).__name__}: {error}"[:300]
            with self._lock:
                self._last_message = self.clock()
                self._messages += 1
            event = parse_message(message)
            if event is None:
                continue
            try:
                self.on_event(event)
            except Exception as error:  # noqa: BLE001 - işleyici hatası akışı durdurmasın
                self._last_error = f"Olay işlenemedi: {type(error).__name__}: {error}"[:300]
        return "durduruldu"

    @staticmethod
    def _close_quietly(connection: Connection) -> None:
        with contextlib.suppress(Exception):
            connection.close()


__all__ = [
    "BookEvent",
    "Event",
    "KlineEvent",
    "MarketStream",
    "StreamStatus",
    "TickerEvent",
    "default_connector",
    "parse_message",
    "stream_names",
    "stream_url",
]
