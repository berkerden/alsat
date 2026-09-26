"""Canlı döngü: piyasa akışını kâğıt işlem motoruna ve öneri motoruna bağlar.

Arayüz sunucusu açılınca arka planda başlar. İşleri:

1. **Açılış.** Borsa filtrelerini (günde bir) ve sunucu saatini alır.
   Uygulama kapalıyken kaçırılan 1 dakikalık mumları REST ile çeker ve kâğıt
   motoruna **çevrimdışı** işletir (borsa tarafı olur, uygulama tarafı
   ertelenir), sonra ertelenen işleri yapar. 15m ve 1h deposunu tamamlar.
2. **Akış.** WebSocket'ten gelen kapanmış 1m mum kâğıt emirlerini ilerletir;
   kapanmış 15m/1h mum depoya yazılır ve öneri motoru çalışır. Yeni bir AL
   sinyali bildirim olur; coin Kâğıt İşlem modundaysa risk kapılarından
   geçerse kâğıt emri açılır, Demo Mode'daysa sinyal Demo yürütücüsüne
   (``execution.executor``) gider.
3. **Nöbet.** Saniyede bir: uyku/uyanma (duvar saati ile tekdüze saat
   arasındaki sıçrama), akış sessizliği, yedek REST yoklaması (akış 90
   saniyeden uzun kopuksa), nabız kaydı. Kâğıt işlemde coin varken Mac'in
   kendiliğinden uyuması engellenir (``core.awake``).

Ağ istekleri ``RequestBudget``'tan geçer; bütçe izin vermezse istek
gönderilmez ve sağlık panelinde görünür.

Bu modül API anahtarı kullanmaz ve kendisi emir göndermez; "emir" burada
kâğıt defterine yazılan kayıttır. Demo emirlerini yalnızca Demo yürütücüsü
gönderir.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from albsat.core.audit import SOURCE_LOOP, SOURCE_SYSTEM
from albsat.core.awake import SleepGuard
from albsat.core.clock import from_ms, iso, istanbul_text, parse_utc, to_ms, utc_now
from albsat.core.filters import SymbolRules
from albsat.data.backfill import fetch_range
from albsat.data.exchangeinfo import ExchangeInfoStore, refresh
from albsat.data.klines import closed_only, interval_ms, parse_klines
from albsat.data.live import USDTTRY, LiveMarket
from albsat.data.store import KlineStore
from albsat.exchange.http import HttpError, PublicHttp
from albsat.exchange.market_stream import (
    BookEvent,
    Event,
    KlineEvent,
    MarketStream,
    TickerEvent,
    stream_names,
    stream_url,
)
from albsat.exchange.ratelimit import BudgetExceeded
from albsat.modes.state import LIVE_MODES, MODE_DEMO, MODE_OFF, MODE_PAPER
from albsat.notify.base import KIND_CONNECTION, KIND_SIGNAL, KIND_SYSTEM, Notifier
from albsat.paper.engine import OrderMeta, PaperEngine
from albsat.paper.fills import Candle
from albsat.risk.engine import SOURCE_RULE, OrderIntent
from albsat.strategy.card import SignalCard
from albsat.strategy.journal import SignalJournal
from albsat.strategy.rules import Rule, RuleSet, RuleStoreError
from albsat.strategy.signals import EngineConfig, recommend

logger = logging.getLogger(__name__)

MINUTE_MS = 60_000
STATE_HEARTBEAT = "son_nabiz_utc"

#: Uygulama bundan uzun kapalı kaldıysa açılışta bildirim gider.
OFFLINE_NOTICE_SECONDS = 180
#: Kaçırılan 1m mumlar en fazla bu kadar geriye çekilir (≈ 44 istek/coin).
MAX_CATCHUP_DAYS = 30
#: 15m/1h deposunun açılışta REST ile tamamlanacak en fazla sayfası.
MAX_PERIOD_PAGES = 10
#: Duvar saati tekdüze saatten bu kadar fazla ilerlediyse Mac uyumuştu.
SLEEP_JUMP_SECONDS = 30.0
#: Akış bu kadar süre bağlı değilse yedek REST yoklaması başlar.
FALLBACK_AFTER_SECONDS = 90.0
#: Bağlantı kopukluğu bu kadar sürerse bildirim gider.
DISCONNECT_NOTICE_SECONDS = 60.0
#: Sinyal mumu bundan eskiyse kâğıt emir açılmaz (gecikmiş sinyal).
SIGNAL_MAX_AGE_SECONDS = 120
EXCHANGE_INFO_MAX_AGE_DAYS = 1.0
FALLBACK_BOOK_SECONDS = 10.0
FALLBACK_TICKER_SECONDS = 60.0
HEARTBEAT_SECONDS = 30.0
#: Uyku engelinin gerekip gerekmediği bu sıklıkla sınanır.
SLEEP_GUARD_SECONDS = 5.0
#: Açılış uzlaştırması (en fazla 30 günlük 1m mum) bu süreden uzun sürerse
#: döngü "takıldı" sayılır (Faz 7 gözcüsü).
STARTUP_GRACE_SECONDS = 900.0


@dataclass
class RunnerHealth:
    baslangic_utc: str | None = None
    son_uzlastirma_utc: str | None = None
    son_uzlastirma_ozet: str | None = None
    saat_farki_ms: int | None = None
    filtre_tazeleme_utc: str | None = None
    yedek_yoklama: bool = False
    uyku_sayisi: int = 0
    son_uyanma_utc: str | None = None
    son_hata: str | None = None
    kopukluk_baslangic: float | None = None
    kopukluk_bildirildi: bool = False
    olaylar: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        stamp = istanbul_text(utc_now())
        self.olaylar.append(f"{stamp} — {text}")
        del self.olaylar[:-30]


class LiveRunner:
    def __init__(
        self,
        root: Path | str,
        *,
        symbols: Sequence[str],
        periods: Sequence[str],
        engine: PaperEngine,
        market: LiveMarket,
        http: PublicHttp,
        notifier: Notifier,
        stream_base: str,
        ruleset_loader: Callable[[], RuleSet | None],
        stream_factory: Callable[..., MarketStream] | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep_guard: SleepGuard | None = None,
    ) -> None:
        self.root = Path(root)
        self.symbols = tuple(symbols)
        self.periods = tuple(periods)
        self.engine = engine
        self.market = market
        self.http = http
        self.notifier = notifier
        self.ruleset_loader = ruleset_loader
        self.clock = clock
        self.monotonic = monotonic
        self.store = KlineStore(self.root)
        self.info_store = ExchangeInfoStore(self.root)
        self.journal = SignalJournal.in_directory(self.root)
        self.health = RunnerHealth()
        self._rules_cache: dict[str, SymbolRules | None] = {}
        self._queue: queue.Queue[Event | None] = queue.Queue(maxsize=10_000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_wall = clock()
        self._last_mono = monotonic()
        self._last_book_poll = 0.0
        self._last_ticker_poll = 0.0
        self._last_minute_poll = -1
        self._last_heartbeat = 0.0
        self._last_guard = -SLEEP_GUARD_SECONDS
        #: Faz 7 gözcüsü için: döngü ne zaman başladı, son turu ne zamandı (tekdüze saat).
        self._started_mono: float | None = None
        self._loop_mono: float | None = None
        self.sleep_guard = sleep_guard or SleepGuard()
        #: Faz 5: Demo yürütücüsü (``execution.executor.DemoExecutor``); çalışma
        #: zamanı kurar. Coin Demo Mode'dayken sinyal ona gider.
        self.demo: Any = None
        self.live: Any = None
        names = stream_names(self.symbols, ("1m", *self.periods), extra_tickers=(USDTTRY,))
        factory = stream_factory or MarketStream
        self.stream = factory(
            stream_url(stream_base, names), on_event=self._on_stream_event,
            on_state=self._on_stream_state,
        )

    # --- yaşam döngüsü ------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="canli-dongu", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        self.stream.stop()
        if self._thread is not None:
            self._thread.join(10)
        self.sleep_guard.stop()
        self._write_heartbeat()
        self.engine.audit.write("kapanis", "Uygulama kapatıldı", kaynak=SOURCE_SYSTEM)
        self.notifier.send("⏻ Uygulama kapatıldı. Kâğıt emirler uygulama açılınca "
                           "kaldığı yerden işlenecek.", kind=KIND_CONNECTION)

    def _run(self) -> None:
        self._started_mono = self.monotonic()
        self._loop_mono = None
        try:
            self.bootstrap()
        except Exception as error:  # noqa: BLE001 - açılış hatası döngüyü öldürmesin
            logger.exception("Açılış uzlaştırması başarısız")
            self.health.son_hata = f"Açılış: {type(error).__name__}: {error}"[:300]
        self.stream.start()
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                event = None
            try:
                if event is not None:
                    self.handle(event)
                self.tick()
            except Exception as error:  # noqa: BLE001 - tek hata döngüyü durdurmasın
                logger.exception("Canlı döngü hatası")
                self.health.son_hata = f"{type(error).__name__}: {error}"[:300]
            self._loop_mono = self.monotonic()

    # --- gözcü (Faz 7) ---------------------------------------------------------

    def loop_age(self) -> float | None:
        """Döngünün son turundan bu yana geçen saniye.

        ``None``: döngü hiç başlamadı ya da açılış uzlaştırması hâlâ süresi
        içinde. Açılış :data:`STARTUP_GRACE_SECONDS`'tan uzun sürerse açılıştan
        bu yana geçen süre döner (takılmış sayılır)."""
        started = self._started_mono
        if started is None:
            return None
        now = self.monotonic()
        last = self._loop_mono
        if last is None:
            elapsed = now - started
            return elapsed if elapsed > STARTUP_GRACE_SECONDS else None
        return now - last

    def stale_symbols(self, max_age_seconds: float) -> list[str]:
        """Fiyatı ``max_age_seconds``'tan eski (ya da hiç gelmemiş) coinler.

        Döngü açılışı bitirmeden boş liste döner; uzlaştırma sürerken fiyat
        gelmemesi beklenen durumdur. Hiç fiyat gelmemiş coin de ancak
        açılıştan bu yana ``max_age_seconds`` geçtiyse sayılır: akış yeni
        bağlanırken yapılan yoklama yanlış alarm vermesin."""
        if self._loop_mono is None:
            return []
        started = self._started_mono
        waited = self.monotonic() - started if started is not None else max_age_seconds + 1
        now = self.clock()
        stale = []
        for sembol in self.symbols:
            seen = self.market.last_event(sembol)
            if seen is None:
                if waited > max_age_seconds:
                    stale.append(sembol)
            elif (now - seen).total_seconds() > max_age_seconds:
                stale.append(sembol)
        return stale

    # --- akış olayları ---------------------------------------------------

    def _on_stream_event(self, event: Event) -> None:
        """Akış iş parçacığında çalışır: fiyatlar hemen, mumlar kuyruğa."""
        if isinstance(event, BookEvent):
            self.market.on_book(event.sembol, event.alis, event.satis)
        elif isinstance(event, TickerEvent):
            self.market.on_ticker(event.sembol, event.son_fiyat, event.hacim_quote_24s)
            if event.sembol == USDTTRY:
                self.engine.usdttry = event.son_fiyat
        elif isinstance(event, KlineEvent) and event.kapandi:
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                self.health.son_hata = "Olay kuyruğu doldu; mum atlandı, boşluk REST ile dolacak."

    def _on_stream_state(self, connected: bool, reason: str) -> None:
        if connected:
            started = self.health.kopukluk_baslangic
            if started is not None and self.health.kopukluk_bildirildi:
                seconds = self.monotonic() - started
                self.notifier.send(
                    f"🔌 Bağlantı geri geldi ({seconds:.0f} sn kopuktu). Kaçırılan mumlar "
                    "işleniyor.",
                    kind=KIND_CONNECTION,
                )
            self.health.kopukluk_baslangic = None
            self.health.kopukluk_bildirildi = False
            self.health.yedek_yoklama = False
            self.health.note("Piyasa akışına bağlandı")
        else:
            if self.health.kopukluk_baslangic is None:
                self.health.kopukluk_baslangic = self.monotonic()
            self.health.note(f"Piyasa akışı kapalı: {reason}")

    def handle(self, event: Event) -> None:
        if not isinstance(event, KlineEvent) or not event.kapandi:
            return
        if event.periyot == "1m":
            self._on_minute(event.sembol, event.mum)
        elif event.periyot in self.periods:
            self._on_period(event.sembol, event.periyot, event.ham)

    def _on_minute(self, sembol: str, candle: Candle, *, online: bool = True) -> None:
        last = self.engine.last_candle_ms(sembol)
        if last is not None and candle.open_time_ms > last + MINUTE_MS:
            # Akış bir süre kopmuş: aradaki mumlar önce işlenir.
            self._catch_up(sembol, until_ms=candle.open_time_ms - 1, online=online)
        self.market.on_minute(sembol, candle)
        self.engine.process_candle(sembol, candle, online=online, now=self.clock())

    def _on_period(self, sembol: str, periyot: str, row: tuple[Any, ...]) -> None:
        step = interval_ms(periyot)
        last = self.store.last_open_time(sembol, periyot)
        if last is not None and int(row[0]) > last + step:
            self.sync_period(sembol, periyot)
        frame = parse_klines([list(row)], interval=periyot, now_ms=to_ms(self.clock()))
        frame = closed_only(frame)
        if not frame.empty:
            self.store.upsert(frame, symbol=sembol, interval=periyot)
        self.evaluate_signals(sembol, periyot)

    # --- REST ile tamamlama --------------------------------------------------

    def symbol_rules(self, sembol: str) -> SymbolRules | None:
        if sembol not in self._rules_cache:
            snapshot = self.info_store.read()
            self._rules_cache[sembol] = snapshot.rules_for(sembol) if snapshot else None
        return self._rules_cache[sembol]

    def _fetch_minutes(self, sembol: str, start_ms: int, end_ms: int) -> list[Candle]:
        """Kapanmış 1m mumlar, borsanın fiyat metinleriyle (``Decimal``)."""
        now_ms = to_ms(self.clock())
        end = min(end_ms, now_ms)
        cursor = start_ms
        candles: list[Candle] = []
        for _ in range(MAX_CATCHUP_DAYS * 2 + 2):
            if cursor > end:
                break
            rows = self.http.klines(
                symbol=sembol, interval="1m", start_time=cursor, end_time=end, limit=1000
            )
            if not rows:
                break
            for row in rows:
                candle = Candle.from_rest(list(row), MINUTE_MS)
                if candle.close_time_ms <= now_ms:
                    candles.append(candle)
            next_cursor = int(rows[-1][0]) + MINUTE_MS
            if next_cursor <= cursor or len(rows) < 1000:
                break
            cursor = next_cursor
        return candles

    def _catch_up(
        self, sembol: str, *, until_ms: int | None = None, online: bool, warm: bool = False
    ) -> tuple[int, Candle | None]:
        """Motorun son işlediği mumdan ``until_ms``'e kadar 1m mumları işler.

        ``(işlenen mum sayısı, alınan son mum)`` döner. ``warm`` açıksa BTC
        hareketi ölçümü için son 60 dakika da yüklenir (açılışta, uyanınca).
        """
        now_ms = to_ms(self.clock())
        until = now_ms if until_ms is None else until_ms
        floor = (now_ms // MINUTE_MS) * MINUTE_MS
        earliest = floor - MAX_CATCHUP_DAYS * 86_400_000
        last = self.engine.last_candle_ms(sembol)
        start = floor - 60 * MINUTE_MS if last is None else last + MINUTE_MS
        if start < earliest:
            self.health.note(
                f"{sembol}: kaçırılan dönem {MAX_CATCHUP_DAYS} günden uzun; yalnızca son "
                f"{MAX_CATCHUP_DAYS} gün işlendi."
            )
            start = earliest
        first = min(start, floor - 60 * MINUTE_MS) if warm else start
        if first > until:
            return 0, None
        candles = self._fetch_minutes(sembol, first, until)
        processed = 0
        for candle in candles:
            self.market.on_minute(sembol, candle)
            if candle.open_time_ms >= start:
                self.engine.process_candle(sembol, candle, online=online, now=self.clock())
                processed += 1
        return processed, (candles[-1] if candles else None)

    def sync_period(self, sembol: str, periyot: str) -> int:
        """15m/1h deposunu REST ile şimdiye kadar tamamlar (en fazla birkaç sayfa)."""
        now_ms = to_ms(self.clock())
        step = interval_ms(periyot)
        last = self.store.last_open_time(sembol, periyot)
        start = (now_ms - 1000 * step) if last is None else last + step
        pages = (now_ms - start) // (1000 * step) + 1
        if pages > MAX_PERIOD_PAGES:
            self.health.note(
                f"{sembol} {periyot}: depo {pages} sayfa geride; açılışta yalnızca son "
                f"{MAX_PERIOD_PAGES} sayfa çekildi. Eksiksiz veri için 'bash kurulum.sh' ile "
                "veri tazelemeyi çalıştırın."
            )
            start = now_ms - MAX_PERIOD_PAGES * 1000 * step
        if start + step > now_ms:
            return 0
        frame = fetch_range(
            self.http, symbol=sembol, interval=periyot, start_time=start, end_time=now_ms,
            now_ms=now_ms, max_pages=MAX_PERIOD_PAGES,
        )
        frame = closed_only(frame)
        if frame.empty:
            return 0
        self.store.upsert(frame, symbol=sembol, interval=periyot)
        return int(len(frame))

    # --- açılış ------------------------------------------------------------------

    def bootstrap(self) -> None:
        now = self.clock()
        self.health.baslangic_utc = iso(now)
        previous = parse_utc(self.engine.ledger.get_state(STATE_HEARTBEAT))
        self._refresh_filters(force=False)
        self._check_time()
        summary = self.reconcile(online=False)
        if previous is not None:
            gap = (now - previous).total_seconds()
            if gap > OFFLINE_NOTICE_SECONDS:
                self.notifier.send(
                    f"⚠️ Uygulama {_duration(gap)} çevrimdışıydı "
                    f"({istanbul_text(previous)} → {istanbul_text(now)}). {summary}",
                    kind=KIND_CONNECTION,
                )
        self.notifier.send(
            f"▶️ Uygulama açıldı; bütün coinler Sadece Öneri modunda. {summary}\n"
            "Komutlar için /yardim.",
            kind=KIND_SYSTEM,
        )
        self._write_heartbeat()

    def reconcile(self, *, online: bool) -> str:
        """Kaçırılan mumları işler, ertelenen işleri yapar; özet cümle döner."""
        processed: dict[str, int] = {}
        for sembol in self.symbols:
            try:
                processed[sembol], last = self._catch_up(sembol, online=online, warm=True)
                if last is not None:
                    self.engine.apply_deferred(sembol, last, now=self.clock())
            except (HttpError, BudgetExceeded, OSError) as error:
                self.health.son_hata = f"{sembol} uzlaştırma: {error}"[:300]
                processed[sembol] = -1
            for periyot in self.periods:
                try:
                    self.sync_period(sembol, periyot)
                except (HttpError, BudgetExceeded, OSError) as error:
                    self.health.son_hata = f"{sembol} {periyot} tamamlama: {error}"[:300]
        parts = [
            f"{sembol}: {count} mum işlendi" if count >= 0 else f"{sembol}: veri alınamadı"
            for sembol, count in processed.items()
        ]
        summary = "Uzlaştırma: " + ", ".join(parts) + "."
        self.health.son_uzlastirma_utc = iso(self.clock())
        self.health.son_uzlastirma_ozet = summary
        self.health.note(summary)
        return summary

    def _refresh_filters(self, *, force: bool) -> None:
        snapshot = self.info_store.read()
        age = snapshot.age_days() if snapshot else None
        if not force and age is not None and age < EXCHANGE_INFO_MAX_AGE_DAYS:
            return
        try:
            refresh(self.info_store, self.http, list(self.symbols))
            self._rules_cache.clear()
            self.health.filtre_tazeleme_utc = iso(self.clock())
            self.health.note("Borsa filtreleri tazelendi")
        except (HttpError, BudgetExceeded, OSError) as error:
            self.health.son_hata = f"Filtre tazeleme: {error}"[:300]

    def _check_time(self) -> None:
        try:
            before = to_ms(self.clock())
            server = self.http.server_time()
            after = to_ms(self.clock())
        except (HttpError, BudgetExceeded, OSError) as error:
            self.health.son_hata = f"Sunucu saati: {error}"[:300]
            return
        self.health.saat_farki_ms = int(server - (before + after) // 2)

    # --- nöbet ----------------------------------------------------------------------

    def tick(self) -> None:
        wall = self.clock()
        mono = self.monotonic()
        wall_delta = (wall - self._last_wall).total_seconds()
        mono_delta = mono - self._last_mono
        self._last_wall, self._last_mono = wall, mono
        if wall_delta - mono_delta > SLEEP_JUMP_SECONDS or mono_delta > SLEEP_JUMP_SECONDS:
            self._on_wake(max(wall_delta, mono_delta))

        status = self.stream.status()
        if not status.bagli:
            started = self.health.kopukluk_baslangic
            if started is None:
                self.health.kopukluk_baslangic = mono
                started = mono
            down = mono - started
            if down > DISCONNECT_NOTICE_SECONDS and not self.health.kopukluk_bildirildi:
                self.health.kopukluk_bildirildi = True
                self.notifier.send(
                    "🔌 Piyasa bağlantısı koptu. Yedek olarak dakikada bir REST ile veri "
                    f"alınıyor. Son hata: {status.son_hata or 'bilinmiyor'}",
                    kind=KIND_CONNECTION,
                )
            if down > FALLBACK_AFTER_SECONDS:
                self.health.yedek_yoklama = True
                self._poll(mono, wall)

        if mono - self._last_guard >= SLEEP_GUARD_SECONDS:
            self._last_guard = mono
            self.sleep_guard.want(bool(self.engine.modes.trading_symbols())
                                  or self._exchange_positions_open())

        if mono - self._last_heartbeat >= HEARTBEAT_SECONDS:
            self._write_heartbeat()
            self._last_heartbeat = mono
            if self.info_store.read() is not None:
                self._refresh_filters(force=False)

    def _exchange_positions_open(self) -> bool:
        """Borsada Demo ya da canlı emir/pozisyon varken Mac uyumasın (koruma izlenir)."""
        for executor in (self.demo, self.live):
            if executor is None:
                continue
            try:
                if executor.ledger.active():
                    return True
            except Exception:  # noqa: BLE001 - uyku kararı bir okuma hatasıyla düşmesin
                return True
        return False

    def _demo_positions_open(self) -> bool:
        """Faz 5 adı."""
        return self._exchange_positions_open()

    def _on_wake(self, seconds: float) -> None:
        self.health.uyku_sayisi += 1
        self.health.son_uyanma_utc = iso(self.clock())
        self.health.note(f"Uyku/donma algılandı ({_duration(seconds)}); uzlaştırılıyor")
        self.stream.reconnect()
        summary = self.reconcile(online=False)
        self.notifier.send(
            f"💤 Mac yaklaşık {_duration(seconds)} uykudaydı ya da uygulama dondu. "
            f"{summary}",
            kind=KIND_CONNECTION,
        )

    def _poll(self, mono: float, wall: datetime) -> None:
        """Yedek REST yoklaması: akış yokken fiyat ve mumlar."""
        try:
            if mono - self._last_book_poll >= FALLBACK_BOOK_SECONDS:
                self._last_book_poll = mono
                for item in self.http.book_tickers(self.symbols):
                    self.market.on_book(
                        str(item["symbol"]), Decimal(str(item["bidPrice"])),
                        Decimal(str(item["askPrice"])),
                    )
            if mono - self._last_ticker_poll >= FALLBACK_TICKER_SECONDS:
                self._last_ticker_poll = mono
                for item in self.http.tickers_24h([*self.symbols, USDTTRY]):
                    self.market.on_ticker(
                        str(item["symbol"]), Decimal(str(item["lastPrice"])),
                        Decimal(str(item["quoteVolume"])),
                    )
                    if item["symbol"] == USDTTRY:
                        self.engine.usdttry = Decimal(str(item["lastPrice"]))
            minute = to_ms(wall) // MINUTE_MS
            # Dakika kapandıktan 3 saniye sonra, dakikada bir kez.
            if minute != self._last_minute_poll and to_ms(wall) % MINUTE_MS >= 3000:
                self._last_minute_poll = minute
                for sembol in self.symbols:
                    self._catch_up(sembol, online=True)
                    for periyot in self.periods:
                        step = interval_ms(periyot)
                        last = self.store.last_open_time(sembol, periyot)
                        due = last is None or last + 2 * step <= to_ms(wall)
                        if due and self.sync_period(sembol, periyot):
                            self.evaluate_signals(sembol, periyot)
        except (HttpError, BudgetExceeded, OSError, KeyError, ValueError) as error:
            self.health.son_hata = f"Yedek yoklama: {error}"[:300]

    def _write_heartbeat(self) -> None:
        self.engine.ledger.set_state(STATE_HEARTBEAT, iso(self.clock()))

    # --- sinyaller ----------------------------------------------------------------

    def _load_ruleset(self) -> RuleSet | None:
        try:
            ruleset = self.ruleset_loader()
        except RuleStoreError as error:
            self.health.son_hata = f"Kural deposu: {error}"[:300]
            return None
        if ruleset is None or ruleset.kosu.teshis_turu:
            return None
        return ruleset

    def evaluate_signals(self, sembol: str, periyot: str) -> list[SignalCard]:
        """Son kapanmış mumda kurallar tetiklendi mi? Yeni AL sinyali için
        bildirim; coin Kâğıt İşlem modundaysa kâğıt emir, Demo Mode'daysa Demo emri,
        Yarı Otomatik'te onay bekleyen canlı öneri, Tam Otomatik'te (kural canlıya
        geçiş kapısını geçtiyse) canlı emir."""
        mode = self.engine.modes.get(sembol)
        if mode == MODE_OFF:
            return []
        ruleset = self._load_ruleset()
        if ruleset is None:
            return []
        limits = self.engine.limits
        result = recommend(
            ruleset,
            sembol=sembol,
            periyot=periyot,
            veri_dizini=self.root,
            config=EngineConfig(
                butce_usdt=str(limits.butce_usdt),
                islem_basi_risk_yuzde=str(limits.islem_basi_risk_yuzde),
            ),
            symbol_rules=self.symbol_rules(sembol),
            try_rate=self.market.usdttry(),
            now=self.clock(),
        )
        fresh: list[SignalCard] = []
        rules = {rule.kimlik: rule for rule in ruleset.for_symbol(sembol, periyot)}
        for card in result.kartlar:
            if card.aksiyon != "AL" or not self.journal.record(card, now=self.clock()):
                continue
            fresh.append(card)
            self.notifier.send(_signal_text(card), kind=KIND_SIGNAL)
            if mode == MODE_PAPER and card.kural_kimligi in rules:
                self.place_from_card(card, rules[card.kural_kimligi])
            elif mode == MODE_DEMO and card.kural_kimligi in rules and self.demo is not None:
                try:
                    self.demo.place_from_card(card, rules[card.kural_kimligi])
                except Exception as error:  # noqa: BLE001 - döngü sürsün, kullanıcı bilsin
                    logger.exception("Demo emri açılamadı")
                    self.health.note(f"{card.sembol} Demo emri açılamadı: {error}")
                    self.notifier.send(f"❌ {card.sembol} sinyali için Demo emri açılamadı: "
                                       f"{type(error).__name__}", kind=KIND_SIGNAL)
            elif mode in LIVE_MODES and card.kural_kimligi in rules and self.live is not None:
                try:
                    self.live.place_from_card(card, rules[card.kural_kimligi])
                except Exception as error:  # noqa: BLE001 - döngü sürsün, kullanıcı bilsin
                    logger.exception("Canlı emir/öneri açılamadı")
                    self.health.note(f"{card.sembol} canlı emir/öneri açılamadı: {error}")
                    self.notifier.send(f"❌ {card.sembol} sinyali için canlı emir/öneri "
                                       f"açılamadı: {type(error).__name__}", kind=KIND_SIGNAL)
        return fresh

    def place_from_card(self, card: SignalCard, rule: Rule) -> None:
        now = self.clock()
        signal_close = parse_utc(card.sinyal_mumu_kapanis_utc)
        if signal_close is not None and (now - signal_close).total_seconds() > (
            SIGNAL_MAX_AGE_SECONDS
        ):
            self.health.note(
                f"{card.sembol} {card.kural_etiketi}: sinyal mumu eski, kâğıt emir açılmadı"
            )
            return
        entry = card.giris
        notes: list[str] = []
        reprice = 0
        quote = self.market.quote(card.sembol)
        if quote is not None and entry >= quote.satis:
            # Fiyat sinyal mumunun kapanışının altına inmiş: limit-maker emir
            # o fiyattan reddedilirdi. En iyi alışa yazılır (daha ucuz giriş).
            entry = quote.alis
            reprice = 1
            notes.append(
                f"Giriş {card.giris} en iyi satışın ({quote.satis}) üstünde kaldığı için "
                f"en iyi alışa ({quote.alis}) yazıldı."
            )
        intent = OrderIntent(
            sembol=card.sembol,
            periyot=card.periyot,
            giris=entry,
            hedef=card.hedef1,
            stop=card.stop,
            kaynak=SOURCE_RULE,
            kural_kimligi=card.kural_kimligi,
        )
        meta = OrderMeta(
            kural_etiketi=card.kural_etiketi,
            sinyal_mumu_utc=card.sinyal_mumu_kapanis_utc,
            gecerlilik_mum=card.gecerlilik_mum,
            azami_tutma_mum=rule.pencere_mum,
            beklenen_hedef_net_yuzde=float(card.net_marj_yuzde),
            beklenen_ortalama_yuzde=float(rule.kanit.test_donemi_net_yuzde),
            yeniden_fiyatlama=reprice,
            notlar=tuple(notes),
        )
        decision, order = self.engine.place(
            intent,
            market=self.market.market_state(card.sembol, card.periyot,
                                            self.symbol_rules(card.sembol)),
            now=now,
            meta=meta,
            source=SOURCE_LOOP,
            best_ask=None if quote is None else quote.satis,
        )
        if order is None:
            self.notifier.send(
                f"ℹ️ {card.sembol} sinyali için kâğıt emir açılmadı.\n{decision.ozet_tr}",
                kind=KIND_SIGNAL,
            )

    # --- dışarıya ----------------------------------------------------------------

    def marks(self) -> dict[str, Decimal]:
        """Değerleme fiyatları: satış tarafı olduğu için en iyi alış."""
        result: dict[str, Decimal] = {}
        for sembol in self.symbols:
            price = self.market.bid(sembol)
            if price is not None:
                result[sembol] = price
        return result

    def status(self) -> dict[str, Any]:
        stream = self.stream.status()
        budget = self.http.budget.status() if self.http.budget else None
        now = self.clock()
        return {
            "akis_bagli": stream.bagli,
            "akis_son_mesaj_sn": None if stream.son_mesaj_saniye_once is None
            else round(stream.son_mesaj_saniye_once, 1),
            "akis_yeniden_baglanma": stream.yeniden_baglanma,
            "akis_mesaj": stream.mesaj_sayisi,
            "akis_son_hata": stream.son_hata,
            "yedek_yoklama": self.health.yedek_yoklama,
            "saat_farki_ms": self.health.saat_farki_ms,
            "baslangic_utc": self.health.baslangic_utc,
            "son_uzlastirma_utc": self.health.son_uzlastirma_utc,
            "son_uzlastirma_ozet": self.health.son_uzlastirma_ozet,
            "filtre_tazeleme_utc": self.health.filtre_tazeleme_utc,
            "uyku_sayisi": self.health.uyku_sayisi,
            "son_uyanma_utc": self.health.son_uyanma_utc,
            "uyku_engeli": self.sleep_guard.status().as_dict(),
            "son_hata": self.health.son_hata,
            "olaylar": list(reversed(self.health.olaylar)),
            "istek_butcesi": None if budget is None else {
                "yerel_1dk": budget.yerel_1dk,
                "yerel_tavan": budget.yerel_tavan,
                "borsa_1dk": budget.borsa_1dk,
                "borsa_siniri": 6000,
                "toplam_istek": budget.toplam_istek,
                "reddedilen": budget.reddedilen,
                "bekleme_saniye": round(budget.bekleme_saniye, 1),
                "engelli": budget.engelli,
                "son_hata": budget.son_hata,
            },
            "coinler": [self._coin_status(sembol, now) for sembol in self.symbols],
            "usdttry": _text(self.market.usdttry()),
        }


    def _coin_status(self, sembol: str, now: datetime) -> dict[str, Any]:
        seen = self.market.last_event(sembol)
        processed = self.engine.last_candle_ms(sembol)
        return {
            "sembol": sembol,
            "son_fiyat": _text(self.market.last_price(sembol)),
            "veri_yasi_sn": None if seen is None else round((now - seen).total_seconds(), 1),
            "spread_gozlem": self.market.spread_samples(sembol),
            "son_islenen_1m_utc": None if processed is None else iso(from_ms(processed)),
        }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} sn"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} dk"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} sa {minutes} dk"
    return f"{hours // 24} gün {hours % 24} sa"


def _signal_text(card: SignalCard) -> str:
    return (
        f"📈 SİNYAL — {card.sembol} {card.periyot}: {card.aksiyon}\n"
        f"Kural: {card.kural_etiketi}\n"
        f"Giriş {card.giris}, hedef {card.hedef1}, stop {card.stop}\n"
        f"Hedefte net %{card.net_marj_yuzde:.2f}, stopta net %{card.stop_net_marj_yuzde:.2f}\n"
        f"Geçerlilik: {card.gecerlilik_bitis_istanbul}'a kadar.\n"
        "Geçmiş veriye dayalı istatistiksel çıkarımdır, yatırım tavsiyesi değildir."
    )


__all__ = ["LiveRunner", "RunnerHealth"]
