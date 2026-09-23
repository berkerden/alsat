"""Demo Mode hesap olayları akışı (User Data Stream, WebSocket API).

Emirlerin dolumu, iptali ve bakiyeler buradan **anında** gelir. Kurallar
``web-socket-api.md`` ve ``user-data-stream.md``'den:

* Adres: Demo Mode WebSocket API (``wss://demo-ws-api.binance.com/ws-api/v3``).
* Abonelik ``userDataStream.subscribe.signature`` ile yapılır: ``apiKey`` ve
  ``timestamp`` alfabetik sırayla ``anahtar=değer&...`` biçiminde yazılır,
  Ed25519 ile imzalanır. Oturum açmak (``session.logon``) gerekmez; eski
  ``listenKey`` yöntemi kullanılmaz.
* Olaylar ``{"subscriptionId": .., "event": {...}}`` biçiminde gelir:
  ``executionReport`` (emir durumu ve dolumlar), ``listStatus`` (emir
  listeleri), ``outboundAccountPosition`` (bakiyeler).
* Bağlantı en fazla 24 saat yaşar; süre dolmadan yenilenir. Sunucu
  ``serverShutdown`` gönderirse ya da ``eventStreamTerminated`` gelirse
  yeniden bağlanılır.

**Kopukluk.** Bağlantı koptuğu sürede gelen olaylar kaybolur; akış bunları
sonradan göndermez. Bu yüzden her (yeniden) bağlanmada ``on_state(True)``
çağrılır ve yürütücü borsayla **uzlaştırma** yapar: emirleri kimlikleriyle
sorgular, eksik dolumları ``myTrades``'ten tamamlar. Doğruluk kaynağı
borsadır, bu akış yalnızca hızlı haberdir.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from albsat.exchange.signed import StoredKey, sign

RENEW_AFTER_SECONDS = 23 * 3600 + 30 * 60
#: Hesap akışında olay seyrek gelir; ölçüt mesaj değil sunucunun ping'i.
#: ``websockets`` pong'u kendisi gönderir, ölü bağlantıyı ``recv`` hatasıyla bildirir.
SUBSCRIBE_TIMEOUT_SECONDS = 15.0
MAX_ATTEMPTS_PER_5_MIN = 20


class DuplexConnection(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


Connector = Callable[[str], DuplexConnection]


def default_connector(url: str) -> DuplexConnection:
    """``websockets`` ile bağlanır; TLS doğrulaması açıktır (``core.tls``)."""
    from websockets.sync.client import connect

    return connect(url, open_timeout=15, ping_interval=20, ping_timeout=20,
                   close_timeout=5, max_size=2**20)


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Sayı okunamadı: {value!r}") from error


@dataclass(frozen=True)
class OrderUpdate:
    """Bir emrin durumu: akıştaki ``executionReport`` ya da REST sorgusu."""

    sembol: str
    istemci_kimligi: str
    #: İptal olayında iptal edilen emrin asıl kimliği (akışta ``C``).
    asil_istemci_kimligi: str
    taraf: str
    tur: str
    durum: str
    #: ``NEW``, ``TRADE``, ``CANCELED``, ``EXPIRED``... (REST'te boş).
    olay: str
    emir_kimligi: int
    liste_kimligi: int
    fiyat: Decimal
    stop_fiyati: Decimal
    miktar: Decimal
    dolan: Decimal
    dolan_quote: Decimal
    son_dolum_miktari: Decimal
    son_dolum_fiyati: Decimal
    son_dolum_quote: Decimal
    komisyon: Decimal
    komisyon_varligi: str | None
    islem_kimligi: int
    maker: bool
    zaman_ms: int
    bitis_sebebi: str | None
    red_sebebi: str | None

    @property
    def kimlik(self) -> str:
        """Bu olayın ait olduğu emrin kimliği (iptalde asıl kimlik)."""
        return self.asil_istemci_kimligi or self.istemci_kimligi

    @property
    def dolum_var(self) -> bool:
        return self.olay == "TRADE" and self.islem_kimligi >= 0

    @classmethod
    def from_event(cls, data: dict[str, Any]) -> OrderUpdate:
        commission_asset = data.get("N")
        return cls(
            sembol=str(data["s"]),
            istemci_kimligi=str(data.get("c") or ""),
            asil_istemci_kimligi=str(data.get("C") or ""),
            taraf=str(data.get("S") or ""),
            tur=str(data.get("o") or ""),
            durum=str(data.get("X") or ""),
            olay=str(data.get("x") or ""),
            emir_kimligi=int(data.get("i", -1)),
            liste_kimligi=int(data.get("g", -1)),
            fiyat=_dec(data.get("p")),
            stop_fiyati=_dec(data.get("P")),
            miktar=_dec(data.get("q")),
            dolan=_dec(data.get("z")),
            dolan_quote=_dec(data.get("Z")),
            son_dolum_miktari=_dec(data.get("l")),
            son_dolum_fiyati=_dec(data.get("L")),
            son_dolum_quote=_dec(data.get("Y")),
            komisyon=_dec(data.get("n")),
            komisyon_varligi=None if commission_asset in (None, "") else str(commission_asset),
            islem_kimligi=int(data.get("t", -1)),
            maker=bool(data.get("m", False)),
            zaman_ms=int(data.get("T") or data.get("E") or 0),
            bitis_sebebi=None if data.get("eR") in (None, "") else str(data.get("eR")),
            red_sebebi=None if data.get("r") in (None, "", "NONE") else str(data.get("r")),
        )

    @classmethod
    def from_rest(cls, data: dict[str, Any]) -> OrderUpdate:
        """``GET /api/v3/order`` ya da emir yanıtındaki ``orderReports`` satırı."""
        return cls(
            sembol=str(data.get("symbol", "")),
            istemci_kimligi=str(data.get("clientOrderId") or ""),
            asil_istemci_kimligi=str(data.get("origClientOrderId") or ""),
            taraf=str(data.get("side") or ""),
            tur=str(data.get("type") or ""),
            durum=str(data.get("status") or ""),
            olay="",
            emir_kimligi=int(data.get("orderId", -1)),
            liste_kimligi=int(data.get("orderListId", -1)),
            fiyat=_dec(data.get("price")),
            stop_fiyati=_dec(data.get("stopPrice")),
            miktar=_dec(data.get("origQty")),
            dolan=_dec(data.get("executedQty")),
            dolan_quote=_dec(data.get("cummulativeQuoteQty")),
            son_dolum_miktari=Decimal("0"),
            son_dolum_fiyati=Decimal("0"),
            son_dolum_quote=Decimal("0"),
            komisyon=Decimal("0"),
            komisyon_varligi=None,
            islem_kimligi=-1,
            maker=False,
            zaman_ms=int(data.get("updateTime") or data.get("transactTime")
                         or data.get("time") or 0),
            bitis_sebebi=None if data.get("expiryReason") in (None, "")
            else str(data.get("expiryReason")),
            red_sebebi=None,
        )


@dataclass(frozen=True)
class ListUpdate:
    sembol: str
    liste_istemci_kimligi: str
    liste_kimligi: int
    tur: str
    durum_turu: str
    liste_durumu: str
    emirler: tuple[str, ...]
    zaman_ms: int


@dataclass(frozen=True)
class BalanceUpdate:
    bakiyeler: dict[str, tuple[Decimal, Decimal]]
    zaman_ms: int


@dataclass(frozen=True)
class StreamNotice:
    """Akışın kendisiyle ilgili haber (sunucu kapanıyor, abonelik bitti)."""

    tur: str


UserEvent = OrderUpdate | ListUpdate | BalanceUpdate | StreamNotice


def parse_user_message(text: str | bytes) -> UserEvent | None:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("event")
    if not isinstance(data, dict):
        return None
    kind = data.get("e")
    try:
        if kind == "executionReport":
            return OrderUpdate.from_event(data)
        if kind == "listStatus":
            return ListUpdate(
                sembol=str(data.get("s", "")),
                liste_istemci_kimligi=str(data.get("C") or ""),
                liste_kimligi=int(data.get("g", -1)),
                tur=str(data.get("c") or ""),
                durum_turu=str(data.get("l") or ""),
                liste_durumu=str(data.get("L") or ""),
                emirler=tuple(str(item.get("c") or "") for item in data.get("O", ())
                              if isinstance(item, dict)),
                zaman_ms=int(data.get("T") or data.get("E") or 0),
            )
        if kind == "outboundAccountPosition":
            balances = {
                str(item["a"]): (_dec(item.get("f")), _dec(item.get("l")))
                for item in data.get("B", ()) if isinstance(item, dict)
            }
            return BalanceUpdate(balances, int(data.get("u") or data.get("E") or 0))
        if kind in ("eventStreamTerminated", "serverShutdown"):
            return StreamNotice(str(kind))
    except (KeyError, ValueError, TypeError):
        return None
    return None


def signature_payload(params: dict[str, Any]) -> str:
    """WebSocket API imza metni: parametreler alfabetik, ``k=v`` ``&`` ile."""
    return "&".join(f"{key}={params[key]}" for key in sorted(params))


@dataclass(frozen=True)
class UserStreamStatus:
    bagli: bool
    abone: bool
    bagli_oldugu_an: float | None
    son_mesaj_saniye_once: float | None
    yeniden_baglanma: int
    olay_sayisi: int
    son_hata: str | None


class UserStream:
    """Arka planda bağlı kalan, koptuğunda yeniden bağlanıp abone olan hesap akışı."""

    def __init__(
        self,
        url: str,
        *,
        key: StoredKey,
        on_event: Callable[[UserEvent], None],
        on_state: Callable[[bool, str], None] | None = None,
        connector: Connector = default_connector,
        server_time_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self._key = key
        self.on_event = on_event
        self.on_state = on_state
        self.connector = connector
        self.server_time_ms = server_time_ms
        self.clock = clock
        self._stop = threading.Event()
        self._force = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._connection: DuplexConnection | None = None
        self._subscribed = False
        self._connected_at: float | None = None
        self._last_message: float | None = None
        self._reconnects = 0
        self._events = 0
        self._last_error: str | None = None
        self._attempts: deque[float] = deque()
        self._request_id = 0

    def __repr__(self) -> str:
        return f"UserStream({self.url}, <anahtar gizli>)"

    # --- yaşam döngüsü ------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hesap-akisi", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._close()
        if self._thread is not None:
            self._thread.join(timeout)

    def reconnect(self) -> None:
        self._force.set()
        self._close()

    def _close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            self._subscribed = False
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()

    def status(self) -> UserStreamStatus:
        with self._lock:
            now = self.clock()
            return UserStreamStatus(
                bagli=self._connection is not None,
                abone=self._subscribed,
                bagli_oldugu_an=self._connected_at,
                son_mesaj_saniye_once=None if self._last_message is None
                else now - self._last_message,
                yeniden_baglanma=self._reconnects,
                olay_sayisi=self._events,
                son_hata=self._last_error,
            )

    # --- iç döngü -----------------------------------------------------

    def _set_state(self, connected: bool, reason: str) -> None:
        if self.on_state is not None:
            with contextlib.suppress(Exception):
                self.on_state(connected, reason)

    def _wait_before_attempt(self, failures: int) -> bool:
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

    def _subscribe(self, connection: DuplexConnection) -> str | None:
        """Abone olur; başarısızsa sebebi döner."""
        self._request_id += 1
        request_id = f"albsat-abone-{self._request_id}"
        params: dict[str, Any] = {
            "apiKey": self._key.api_key.reveal(),
            "timestamp": self.server_time_ms(),
        }
        params["signature"] = sign(signature_payload(params), self._key.private_der_b64)
        connection.send(json.dumps({"id": request_id,
                                    "method": "userDataStream.subscribe.signature",
                                    "params": params}))
        deadline = self.clock() + SUBSCRIBE_TIMEOUT_SECONDS
        while self.clock() < deadline and not self._stop.is_set():
            try:
                message = connection.recv(timeout=2.0)
            except TimeoutError:
                continue
            try:
                payload = json.loads(message)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get("id") != request_id:
                if isinstance(parse_user_message(message), StreamNotice):
                    self._force.set()
                    return "Sunucu akışı abonelik sırasında kapattı; yeniden bağlanılıyor."
                # Abonelikten önce gelen olay: işlenir, kaybolmaz.
                self._dispatch(message)
                continue
            if payload.get("status") == 200:
                return None
            error = payload.get("error") or {}
            return (f"Hesap akışına abone olunamadı: {error.get('code')} "
                    f"{error.get('msg', '')}").strip()[:300]
        return "Hesap akışı abonelik yanıtı gelmedi."

    def _dispatch(self, message: str | bytes) -> None:
        event = parse_user_message(message)
        if event is None:
            return
        with self._lock:
            self._events += 1
        try:
            self.on_event(event)
        except Exception as error:  # noqa: BLE001 - işleyici hatası akışı durdurmasın
            self._last_error = f"Olay işlenemedi: {type(error).__name__}: {error}"[:300]

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if not self._wait_before_attempt(failures):
                break
            try:
                connection = self.connector(self.url)
            except Exception as error:  # noqa: BLE001 - bağlantı hatası yeniden denenir
                failures += 1
                self._last_error = f"Bağlanılamadı: {type(error).__name__}: {error}"[:300]
                self._set_state(False, self._last_error)
                continue
            with self._lock:
                self._connection = connection
                self._connected_at = self.clock()
                self._last_message = self.clock()
            self._force.clear()
            try:
                problem = self._subscribe(connection)
            except Exception as error:  # noqa: BLE001 - kopma yeniden bağlanmayla çözülür
                problem = f"Abonelik sırasında koptu: {type(error).__name__}: {error}"[:300]
            if problem is not None:
                failures += 1
                self._last_error = problem
                self._close_quietly(connection)
                with self._lock:
                    self._connection = None
                    self._connected_at = None
                self._set_state(False, problem)
                continue
            with self._lock:
                self._subscribed = True
            if failures or self._reconnects:
                self._reconnects += 1
            failures = 0
            self._set_state(True, "hesap akışına abone olundu")
            reason = self._pump(connection)
            with self._lock:
                self._connection = None
                self._connected_at = None
                self._subscribed = False
            self._close_quietly(connection)
            if self._stop.is_set():
                break
            self._last_error = reason
            self._set_state(False, reason)
            if not self._force.is_set():
                failures = max(failures, 1)

    def _pump(self, connection: DuplexConnection) -> str:
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
                continue
            except Exception as error:  # noqa: BLE001 - kopma yeniden bağlanmayla çözülür
                return f"Hesap akışı koptu: {type(error).__name__}: {error}"[:300]
            with self._lock:
                self._last_message = self.clock()
            event = parse_user_message(message)
            if isinstance(event, StreamNotice):
                self._force.set()
                return f"Sunucu akışı kapattı ({event.tur}); yeniden bağlanılıyor"
            if event is not None:
                self._dispatch(message)
        return "durduruldu"

    @staticmethod
    def _close_quietly(connection: DuplexConnection) -> None:
        with contextlib.suppress(Exception):
            connection.close()


__all__ = [
    "BalanceUpdate",
    "ListUpdate",
    "OrderUpdate",
    "StreamNotice",
    "UserEvent",
    "UserStream",
    "UserStreamStatus",
    "default_connector",
    "parse_user_message",
    "signature_payload",
]
