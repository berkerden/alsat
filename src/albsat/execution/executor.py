"""Demo Mode emir yürütücüsü (SPEC.md §4.5, §4.6; FAZ0-MIMARI.md Risk #1).

Kâğıt motorunun Demo Mode karşılığı: risk motorundan geçen bir emir niyetini
Binance Demo Mode hesabında gerçek emirlere çevirir ve pozisyonu kapanana
kadar izler. Borsanın söylediği her zaman doğrudur; yürütücünün kaydı
borsayla her bağlanmada, uyanmada ve beş dakikada bir uzlaştırılır.

**Emir akışı.** Giriş ``LIMIT_MAKER`` alıştır ve OTOCO listesinin çalışan
emridir. Giriş **tamamen** dolunca borsa hedef (``LIMIT_MAKER`` satış) ve
stop (``STOP_LOSS``) emirlerini kendisi koyar; uygulama o an kapalı olsa
bile pozisyon korunur.

**Tek değişmez kural: elde coin varsa borsada canlı bir stop olmalı.** Her
turda her pozisyon için bu sınanır. Stop yoksa pozisyon korumasızdır ve
sebebi ne olursa olsun (kısmi dolum, stopun reddi, fiyat aralığı kuralıyla
süresi dolan stop, yarım dolan hedef) aynı yoldan korunur:

1. Çalışan bir emir kısmen dolmuşsa (giriş ya da hedef) en fazla
   ``korumasiz_azami_saniye`` beklenir; dolmazsa liste iptal edilir.
2. Hiçbir emir çalışmıyorsa dolan miktar için hemen yeni bir OCO (hedef +
   stop) kurulur. Fiyat stopun altına inmiş ya da hedefin üstüne çıkmışsa
   OCO kurulamaz (borsa reddeder); bunun yerine fiyat sınırlı, anında-ya-
   iptal bir satışla (``LIMIT IOC``) çıkılır.
3. Satılacak miktar borsanın en küçük emir sınırının altındaysa satılamaz;
   bu kalan küsurat olarak kaydedilir ve kullanıcıya söylenir.

Korumasız geçen süre her pozisyonda ölçülür ve raporlanır.

**Belirsiz sonuç.** Emir isteğinin sonucu bilinmiyorsa (zaman aşımı, 5xx)
emir **aynı kimlikle sorgulanır**, asla yeni kimlikle yeniden gönderilmez.
Borsa ``timestamp + recvWindow``'dan sonra isteği kabul etmediği için, o
süre geçtikten sonra "bu kimlik yok" cevabı "emir hiç ulaşmadı" demektir.

**Yalnızca kendi emirleri.** Kimliği ``albsat-demo-`` ile başlamayan
emirlere dokunulmaz; uzlaştırmada sayılır ve raporlanır. Kendi önekini
taşıyan ama kayıtta olmayan bir alış emri (yetim) iptal edilir, çünkü
dolarsa korumasız bir pozisyon açar.

**Kilit sırası.** Yürütücü kendi kilidini tutarken kâğıt motorunun
``halt``'ını çağırabilir; motorun durdurma kancası ise yürütücünün kilidini
almaz, yalnızca istek bırakır. Böylece iki kilit ters sırayla alınmaz.
"""

from __future__ import annotations

import contextlib
import csv
import io
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

from albsat.core.audit import SOURCE_LOOP, SOURCE_RISK, SOURCE_SYSTEM, AuditLog
from albsat.core.clock import (
    day_start,
    from_ms,
    iso,
    istanbul_text,
    parse_utc,
    to_ms,
    utc_now,
)
from albsat.core.costs import FeePayment, LegCost, RoundTrip
from albsat.core.fees import Liquidity, Side
from albsat.core.filters import SymbolRules
from albsat.core.money import ONE_HUNDRED, ZERO, format_for_api
from albsat.data.commission import DEMO_FILENAME, CommissionStore, paper_costs
from albsat.data.exchangeinfo import ExchangeInfoStore, refresh
from albsat.data.klines import interval_ms
from albsat.data.live import LiveMarket, Quote
from albsat.exchange.signed import RECV_WINDOW_MS
from albsat.exchange.trading import DemoTrader, ExchangeError
from albsat.exchange.user_stream import (
    BalanceUpdate,
    ListUpdate,
    OrderUpdate,
    UserEvent,
)
from albsat.execution import errors, ids
from albsat.execution.ledger import (
    EXIT_DUST,
    EXIT_KILL,
    EXIT_LABELS_TR,
    EXIT_MANUAL,
    EXIT_PROTECT,
    EXIT_STOP,
    EXIT_TARGET,
    EXIT_TIME,
    LEG_FINAL,
    LEG_NEVER,
    LEG_UNKNOWN,
    POS_CANCELLED,
    POS_CLOSED,
    POS_EXITING,
    POS_PARTIAL,
    POS_PENDING,
    POS_PROTECTED,
    POS_REJECTED,
    POS_SENDING,
    POS_UNKNOWN,
    POS_UNPROTECTED,
    ROLE_ENTRY,
    ROLE_EXIT,
    ROLE_STOP,
    ROLE_TARGET,
    DemoFill,
    DemoLedger,
    DemoLeg,
    DemoPosition,
    Economics,
    economics,
    net_result,
)
from albsat.execution.planner import Plan, plan_exit, plan_oco, plan_otoco
from albsat.execution.settings import ExecutionSettings
from albsat.modes.state import MODE_DEMO, MODE_LABELS_TR
from albsat.notify.base import (
    KIND_CANCEL,
    KIND_EXIT,
    KIND_FILL,
    KIND_LIMIT,
    KIND_ORDER,
    KIND_SIGNAL,
    KIND_STOP,
    KIND_SYSTEM,
    KIND_TARGET,
    Notifier,
)
from albsat.paper.engine import OrderMeta, PaperEngine, _breach_key, post_only_gate
from albsat.paper.fills import PaperCosts
from albsat.risk import engine as risk
from albsat.risk.market import Gate, MarketState

logger = logging.getLogger(__name__)

STATE_SETTINGS = "ayarlar"
STATE_STREAK_RESET = "art_arda_sifirlama_utc"
STATE_NOTIFIED_BREACHES = "bildirilen_sinirlar"
STATE_BALANCES = "borsa_bakiyeleri"

DEMO_INFO_FILENAME = "exchangeinfo-demo.json"

TICK_SECONDS = 1.0
RECONCILE_EVERY_SECONDS = 300.0
#: Hesap akışı kopukken dolumlar ancak REST ile görülür; daha sık bakılır.
RECONCILE_STREAM_DOWN_SECONDS = 15.0
#: Borsa isteği ``timestamp + recvWindow``'dan sonra kabul etmez; bu kadar
#: sonra "emir yok" cevabı kesindir.
UNKNOWN_GRACE_MS = RECV_WINDOW_MS + 5000
#: OTOCO'nun hedef/stopu girişin dolmasıyla borsaya konur; olay bu kadar
#: saniyede gelmezse emirler sorgulanır ve korumasızlık bildirilir.
PENDING_ACTIVATION_SECONDS = 5.0
#: Canlı görünen bir çıkış ya da iptal bu kadar süre güncellenmezse sorgulanır.
QUERY_STALE_SECONDS = 5.0
ACTION_COOLDOWN_SECONDS = 2.0
ERROR_COOLDOWN_SECONDS = 5.0
TRADES_REFRESH_SECONDS = 10.0
EXIT_ALERT_ATTEMPTS = 10
QUOTE_MAX_AGE_SECONDS = 15.0
QUOTE_REST_EVERY_SECONDS = 5.0
INFO_MAX_AGE_DAYS = 1.0
BOOTSTRAP_RETRY_SECONDS = 30.0
SLEEP_JUMP_SECONDS = 30.0
TIME_SYNC_EVERY_SECONDS = 3600.0
SIGNAL_MAX_AGE_SECONDS = 120
#: Yeni giriş emri, borsanın emir sayısı sınırının bu oranı dolmuşsa gönderilmez.
#: Koruma emirleri bu yerel sınıra takılmaz.
ORDER_SOFT_CAP_SHORT = Decimal("0.5")
ORDER_SOFT_CAP_DAY = Decimal("0.8")


def _m(value: Decimal) -> str:
    return format_for_api(value)


def _usdt(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass
class ExecutorHealth:
    baslangic_utc: str | None = None
    hazirlik_tamam: bool = False
    hazirlik_sorunu: str | None = None
    son_uzlastirma_utc: str | None = None
    son_uzlastirma_ozet: str | None = None
    uyarilar: list[str] = field(default_factory=list)
    elle_emirler: dict[str, int] = field(default_factory=dict)
    filtre_utc: str | None = None
    komisyon_utc: str | None = None
    son_hata: str | None = None
    olaylar: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.olaylar.append(f"{istanbul_text(utc_now())} — {text}")
        del self.olaylar[:-40]


@dataclass(frozen=True)
class PlaceResult:
    karar: risk.RiskDecision
    pozisyon: DemoPosition | None
    mesaj: str
    plan: Plan | None = None


@dataclass(frozen=True)
class DemoAccountView:
    donem_id: int
    baslangic_usdt: Decimal
    baslangic_utc: str
    nakit_usdt: Decimal
    kilitli_usdt: Decimal
    serbest_usdt: Decimal
    coinler: dict[str, Decimal]
    ozsermaye_usdt: Decimal | None
    getiri_yuzde: Decimal | None
    eksik_fiyat: tuple[str, ...]
    #: Borsadaki gerçek serbest USDT (son uzlaştırmadan); bilinmiyorsa ``None``.
    borsa_serbest_usdt: Decimal | None


@dataclass
class _View:
    position: DemoPosition
    legs: tuple[DemoLeg, ...]
    fills: tuple[DemoFill, ...]
    econ: Economics
    rules: SymbolRules | None

    def live(self, *roles: str) -> list[DemoLeg]:
        return [leg for leg in self.legs if leg.canli and (not roles or leg.rol in roles)]

    @property
    def unresolved(self) -> list[DemoLeg]:
        return [leg for leg in self.legs if leg.belirsiz]

    @property
    def stop_live(self) -> list[DemoLeg]:
        # Bekleyen (PENDING_NEW) stop henüz borsada değildir; korumaz.
        return [leg for leg in self.legs if leg.rol == ROLE_STOP
                and leg.durum in ("NEW", "PARTIALLY_FILLED")]

    @property
    def pending_new(self) -> list[DemoLeg]:
        return [leg for leg in self.legs if leg.durum == "PENDING_NEW"]

    @property
    def held(self) -> Decimal:
        """Bu pozisyonun satması gereken coin: alınan + önceki toz − satılan."""
        return self.econ.elde + self.position.dec("onceki_toz")

    @property
    def sellable(self) -> Decimal:
        held = max(self.held, ZERO)
        return self.rules.round_quantity(held) if self.rules is not None else held


class DemoExecutor:
    """Demo Mode hesabının tek sahibi; bütün emirler buradan gider."""

    def __init__(
        self,
        root: Path | str,
        *,
        symbols: Sequence[str],
        engine: PaperEngine,
        live_market: LiveMarket,
        demo_market: LiveMarket,
        notifier: Notifier,
        trader: DemoTrader | None,
        public: Any = None,
        stream_factory: Callable[..., Any] | None = None,
        book_stream_factory: Callable[..., Any] | None = None,
        key_problem: str | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root)
        self.symbols = tuple(symbols)
        self.engine = engine
        self.live_market = live_market
        self.demo_market = demo_market
        self.notifier = notifier
        self.trader = trader
        self.public = public
        self.key_problem = key_problem
        self.clock = clock
        self.monotonic = monotonic
        self.ledger = DemoLedger.in_directory(self.root)
        self.audit: AuditLog = engine.audit
        self.info_store = ExchangeInfoStore(self.root, DEMO_INFO_FILENAME)
        self.live_info_store = ExchangeInfoStore(self.root)
        self.commission_store = CommissionStore(self.root, DEMO_FILENAME)
        self.health = ExecutorHealth()
        self.lock = threading.RLock()
        self._events: queue.Queue[UserEvent] = queue.Queue(maxsize=10_000)
        self._halt_requests: list[tuple[str, str]] = []
        self._halt_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rules_cache: dict[str, SymbolRules | None] = {}
        self._balances: dict[str, tuple[Decimal, Decimal]] = {}
        self._account_flags: dict[str, Any] = {}
        self._cooldown: dict[int, float] = {}
        self._cancel_sent: dict[str, float] = {}
        self._leg_seen: dict[str, float] = {}
        self._trades_asked: dict[str, float] = {}
        self._unprotected_notified: set[int] = set()
        self._reconcile_reason: str | None = None
        self._last_reconcile = -1e18
        self._last_full_reconcile = -1e18
        self._last_quote_poll: dict[str, float] = {}
        self._last_bootstrap_try = -1e18
        self._last_time_sync = -1e18
        self._last_wall = clock()
        self._last_mono = monotonic()
        self._reconciled = False
        self.stream: Any = None
        self.book_stream: Any = None
        if trader is not None and stream_factory is not None:
            self.stream = stream_factory(on_event=self.on_user_event,
                                         on_state=self.on_stream_state,
                                         server_time_ms=trader.now_ms)
        if book_stream_factory is not None:
            self.book_stream = book_stream_factory(self._on_book_event)
        engine.halt_hooks.append(self.on_halt)
        engine.demo_ready = self.not_ready_reason

    # --- yardımcılar ------------------------------------------------------------

    def _now_ms(self) -> int:
        return to_ms(self.clock())

    def _server_ms(self) -> int:
        return self.trader.now_ms() if self.trader is not None else self._now_ms()

    def rules_for(self, symbol: str) -> SymbolRules | None:
        if symbol not in self._rules_cache:
            snapshot = self.info_store.read() or self.live_info_store.read()
            self._rules_cache[symbol] = snapshot.rules_for(symbol) if snapshot else None
        return self._rules_cache[symbol]

    def _base_asset(self, symbol: str) -> str:
        rules = self.rules_for(symbol)
        if rules is not None and rules.base_asset:
            return rules.base_asset
        return symbol.removesuffix("USDT")

    def costs_for(self, symbol: str) -> PaperCosts:
        return paper_costs(self.root, symbol, filename=DEMO_FILENAME,
                           account_label="Demo hesabından")

    def round_trips(self, symbol: str) -> tuple[RoundTrip, RoundTrip]:
        costs = self.costs_for(symbol)

        def leg(side: Side, liquidity: Liquidity) -> LegCost:
            return LegCost(side=side, liquidity=liquidity, rate=costs.rate(side, liquidity))

        entry = leg(Side.BUY, Liquidity.MAKER)
        return (RoundTrip(entry, leg(Side.SELL, Liquidity.MAKER), FeePayment.FROM_RECEIVED),
                RoundTrip(entry, leg(Side.SELL, Liquidity.TAKER), FeePayment.FROM_RECEIVED))

    def buy_fee_rate(self, symbol: str) -> Decimal:
        costs = self.costs_for(symbol)
        return max(costs.rate(Side.BUY, Liquidity.MAKER), costs.rate(Side.BUY, Liquidity.TAKER))

    @property
    def settings(self) -> ExecutionSettings:
        return ExecutionSettings.from_dict(self.ledger.get_json(STATE_SETTINGS, None))

    def update_settings(self, changes: dict[str, Any], *, source: str) -> ExecutionSettings:
        with self.lock:
            new = self.settings.updated(changes)
            self.ledger.set_json(STATE_SETTINGS, new.as_dict())
            self.audit.write("demo_ayar", "Demo emir ayarları değişti", kaynak=source,
                             ayrinti={key: str(value) for key, value in changes.items()})
            return new

    def _price_of(self, asset: str) -> Decimal | None:
        """Komisyon varlığının (ör. BNB) USDT değeri; bilinmiyorsa ``None``."""
        symbol = f"{asset}USDT"
        quote = self.demo_market.quote(symbol) or self.live_market.quote(symbol)
        return quote.alis if quote is not None else None

    def _econ(self, position: DemoPosition, fills: Sequence[DemoFill]) -> Economics:
        rules = self.rules_for(position.sembol)
        base = self._base_asset(position.sembol)
        quote = rules.quote_asset if rules is not None and rules.quote_asset else "USDT"
        return economics(fills, base_asset=base, quote_asset=quote, price_of=self._price_of)

    def _view(self, position: DemoPosition) -> _View:
        fills = self.ledger.fills(position.id)
        return _View(position, self.ledger.legs(position.id), fills,
                     self._econ(position, fills), self.rules_for(position.sembol))

    def _notify(self, text: str, kind: str) -> None:
        with contextlib.suppress(Exception):
            self.notifier.send(text, kind=kind)

    def _note(self, text: str) -> None:
        self.health.note(text)

    def _cool(self, position_id: int, seconds: float) -> None:
        self._cooldown[position_id] = self.monotonic() + seconds

    # --- hazır olma ------------------------------------------------------------------

    def not_ready_reason(self) -> str | None:
        """Demo Mode seçilebilir mi? Kilit almaz (kâğıt motorunun kilidinden çağrılır)."""
        if self.trader is None:
            return self.key_problem or (
                "Demo Mode anahtarı kurulu değil. Kurmak için: bash kurulum.sh demo-anahtar")
        if self.health.hazirlik_sorunu:
            return self.health.hazirlik_sorunu
        if not self.health.hazirlik_tamam:
            return "Demo bağlantısı hazırlanıyor (saat, filtreler, hesap, uzlaştırma)."
        if self.trader.budget.status().engelli:
            return "Binance IP engeli (418) sürüyor; engel bitene kadar Demo işlem yok."
        if self.stream is not None:
            status = self.stream.status()
            if not status.abone:
                return ("Demo hesap akışına bağlı değil"
                        + (f": {status.son_hata}" if status.son_hata else ".")
                        + " Dolumlar görülemeyeceği için yeni emir açılmaz.")
        if not self._reconciled:
            return "Borsayla uzlaştırma henüz tamamlanmadı."
        return None

    # --- yaşam döngüsü -------------------------------------------------------------

    def start(self) -> None:
        if self.trader is None or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="demo-yurutucu", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.stream is not None:
            self.stream.stop()
        if self.book_stream is not None:
            self.book_stream.stop()
        if self._thread is not None:
            self._thread.join(10)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.health.hazirlik_tamam:
                    mono = self.monotonic()
                    if mono - self._last_bootstrap_try >= BOOTSTRAP_RETRY_SECONDS:
                        self._last_bootstrap_try = mono
                        self.bootstrap()
                self.tick()
            except Exception as error:  # noqa: BLE001 - tek hata döngüyü durdurmasın
                logger.exception("Demo yürütücü hatası")
                self.health.son_hata = f"{type(error).__name__}: {error}"[:300]
            self._stop.wait(TICK_SECONDS)

    def bootstrap(self) -> bool:
        """Saat, filtreler, hesap izinleri, komisyon; sonra akış ve uzlaştırma."""
        trader = self.trader
        if trader is None:
            return False
        with self.lock:
            self.health.baslangic_utc = self.health.baslangic_utc or iso(self.clock())
            try:
                trader.sync_time()
                self._last_time_sync = self.monotonic()
                self._refresh_info()
                problem = self._refresh_account(check_permissions=True)
                if problem is not None:
                    self.health.hazirlik_sorunu = problem
                    self._note(problem)
                    self._notify(f"⛔ Demo Mode kullanılamıyor: {problem}", KIND_SYSTEM)
                    return False
                self._refresh_commission()
            except Exception as error:  # noqa: BLE001 - hazırlık yeniden denenecek
                info = errors.classify(error)
                self.health.son_hata = f"Demo hazırlığı: {info.mesaj_tr}"[:300]
                self._note(self.health.son_hata)
                return False
            self.health.hazirlik_sorunu = None
            if self.stream is not None:
                self.stream.start()
            if self.book_stream is not None:
                self.book_stream.start()
            self.reconcile("açılış", full=True)
            self.health.hazirlik_tamam = True
            self._note("Demo bağlantısı hazır")
            return True

    def _refresh_info(self) -> None:
        snapshot = self.info_store.read()
        age = snapshot.age_days() if snapshot else None
        if age is not None and age < INFO_MAX_AGE_DAYS:
            return
        if self.public is None:
            return
        refresh(self.info_store, self.public, list(self.symbols))
        self._rules_cache.clear()
        self.health.filtre_utc = iso(self.clock())
        self._note("Demo borsa filtreleri tazelendi")

    def _refresh_commission(self) -> None:
        trader = self.trader
        if trader is None:
            return
        measured = self.commission_store.read()
        taken = parse_utc(measured.olcum_utc) if measured else None
        fresh = taken is not None and (self.clock() - taken).total_seconds() < 86400
        if fresh and measured is not None and all(s in measured.tablolar for s in self.symbols):
            return
        payloads = {symbol: trader.commission(symbol) for symbol in self.symbols}
        self.commission_store.write(payloads)
        self.health.komisyon_utc = iso(self.clock())
        self._note("Demo hesabının komisyonu ölçüldü")

    def _refresh_account(self, *, check_permissions: bool = False) -> str | None:
        trader = self.trader
        if trader is None:
            return None
        payload = trader.account()
        balances: dict[str, tuple[Decimal, Decimal]] = {}
        for item in payload.get("balances", ()):
            try:
                balances[str(item["asset"])] = (Decimal(str(item["free"])),
                                                Decimal(str(item["locked"])))
            except (KeyError, ValueError, ArithmeticError):
                continue
        self._balances = balances
        self._account_flags = {key: payload.get(key) for key in
                               ("canTrade", "canWithdraw", "canDeposit", "accountType")}
        self.ledger.set_json(STATE_BALANCES, {
            asset: [str(free), str(locked)] for asset, (free, locked) in balances.items()
        })
        if check_permissions:
            if payload.get("canWithdraw"):
                return ("Demo anahtarında para çekme izni AÇIK görünüyor. Uygulama bu anahtarla "
                        "emir göndermeyi reddediyor; Demo API yönetiminde çekim iznini kapatın.")
            if not payload.get("canTrade"):
                return ("Demo anahtarında Spot işlem izni kapalı. Demo API yönetiminde "
                        "'Spot & Margin Trading' iznini açın.")
        return None

    def _free(self, asset: str) -> Decimal | None:
        item = self._balances.get(asset)
        return item[0] if item is not None else None

    # --- akış olayları --------------------------------------------------------------

    def on_user_event(self, event: UserEvent) -> None:
        """Hesap akışının iş parçacığında çalışır; olayı yalnızca kuyruğa koyar."""
        try:
            self._events.put_nowait(event)
        except queue.Full:
            self._reconcile_reason = "olay kuyruğu doldu"

    def on_stream_state(self, connected: bool, reason: str) -> None:
        if connected:
            self._reconcile_reason = "hesap akışı bağlandı"
            self._note("Demo hesap akışına abone olundu")
        else:
            self._note(f"Demo hesap akışı kapalı: {reason}")

    def _on_book_event(self, event: Any) -> None:
        symbol = getattr(event, "sembol", None)
        bid = getattr(event, "alis", None)
        ask = getattr(event, "satis", None)
        if symbol and bid is not None and ask is not None:
            self.demo_market.on_book(symbol, bid, ask)

    def on_halt(self, title: str, source: str) -> None:
        """Kâğıt motorunun durdurma kancası: kilit almaz, istek bırakır."""
        with self._halt_lock:
            self._halt_requests.append((title, source))

    def _drain(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, OrderUpdate):
                self._apply_update(event, source="akis")
            elif isinstance(event, BalanceUpdate):
                self._balances.update(event.bakiyeler)
            elif isinstance(event, ListUpdate) and "REJECT" in (event.liste_durumu,
                                                                   event.durum_turu):
                self._note(f"{event.sembol} emir listesi reddedildi "
                           f"({event.liste_istemci_kimligi})")

    def _apply_halts(self) -> None:
        with self._halt_lock:
            requests, self._halt_requests = self._halt_requests, []
        for title, source in requests:
            count = 0
            for position in self.ledger.active():
                legs = self.ledger.legs(position.id)
                if any(leg.rol == ROLE_ENTRY and (leg.canli or leg.belirsiz) for leg in legs):
                    self.ledger.update_position(position.id, {
                        "giris_iptal_istegi": f"Otomatik işlem durduruldu: {title}."})
                    count += 1
            self.audit.write("demo_durdurma",
                             f"Demo: {count} bekleyen giriş iptal ediliyor ({title})",
                             kaynak=source)

    # --- tur --------------------------------------------------------------------------

    def tick(self) -> None:
        with self.lock:
            self._drain()
            self._apply_halts()
            if self.trader is None or not self.health.hazirlik_tamam:
                return
            wall, mono = self.clock(), self.monotonic()
            wall_delta = (wall - self._last_wall).total_seconds()
            mono_delta = mono - self._last_mono
            self._last_wall, self._last_mono = wall, mono
            if wall_delta - mono_delta > SLEEP_JUMP_SECONDS or mono_delta > SLEEP_JUMP_SECONDS:
                self._note("Uyku/donma algılandı; Demo hesabı uzlaştırılıyor")
                if self.stream is not None:
                    self.stream.reconnect()
                self._reconcile_reason = "uyanma"
            if mono - self._last_time_sync >= TIME_SYNC_EVERY_SECONDS:
                self._last_time_sync = mono
                with contextlib.suppress(Exception):
                    self.trader.sync_time()
            stream_ok = self.stream is None or self.stream.status().abone
            every = RECONCILE_EVERY_SECONDS if stream_ok else RECONCILE_STREAM_DOWN_SECONDS
            if self._reconcile_reason is not None or mono - self._last_reconcile >= every:
                reason = self._reconcile_reason or (
                    "düzenli uzlaştırma" if stream_ok else "hesap akışı kopuk; REST ile izleniyor")
                full = (self._reconcile_reason is not None
                        or mono - self._last_full_reconcile >= RECONCILE_EVERY_SECONDS)
                self._reconcile_reason = None
                self.reconcile(reason, full=full)
            for position in self.ledger.active():
                try:
                    self._advance(position)
                except Exception as error:  # noqa: BLE001 - bir pozisyon diğerlerini durdurmasın
                    logger.exception("Demo pozisyon adımı başarısız")
                    self.health.son_hata = (
                        f"{position.sembol} #{position.id}: {type(error).__name__}: {error}"
                    )[:300]
                    self._cool(position.id, ERROR_COOLDOWN_SECONDS)

    # --- emir durumu güncelleme ---------------------------------------------------------

    def _apply_update(self, update: OrderUpdate, *, source: str) -> DemoLeg | None:
        client = update.kimlik
        leg = self.ledger.leg(client)
        if leg is None:
            if ids.is_ours(client):
                self._note(f"Kayıtta olmayan bir albsat emri görüldü: {client} ({update.durum})")
            return None
        status = update.durum or leg.durum
        # Eski haber yeni durumu geri almasın. "Ulaşmadı" sayılan bir emir borsada
        # görünürse borsa haklıdır (ör. OTOCO'nun bekleyen bacağı sorguda bulunamadı,
        # sonra giriş dolunca borsada belirdi).
        if leg.durum == "FILLED" or (leg.bitti and leg.durum != LEG_NEVER
                                     and status not in LEG_FINAL):
            status = leg.durum
        changes: dict[str, Any] = {"durum": status}
        if update.emir_kimligi >= 0:
            changes["borsa_emir_kimligi"] = update.emir_kimligi
        if update.liste_kimligi >= 0:
            changes["borsa_liste_kimligi"] = update.liste_kimligi
        if update.dolan > leg.dec("dolan"):
            changes["dolan"] = _m(update.dolan)
        if update.dolan_quote > leg.dec("dolan_quote"):
            changes["dolan_quote"] = _m(update.dolan_quote)
        reason = update.bitis_sebebi or update.red_sebebi
        if reason:
            changes["sebep"] = reason
        elif leg.durum == LEG_NEVER and status != LEG_NEVER:
            changes["sebep"] = "borsada görüldü (önceki sorguda bulunamamıştı)"
        updated = self.ledger.update_leg(client, changes)
        self._leg_seen[client] = self.monotonic()
        if update.dolum_var:
            quote_qty = update.son_dolum_quote or (update.son_dolum_fiyati
                                                   * update.son_dolum_miktari)
            self._add_fill(leg, trade_id=update.islem_kimligi, price=update.son_dolum_fiyati,
                           quantity=update.son_dolum_miktari, quote_qty=quote_qty,
                           commission=update.komisyon, asset=update.komisyon_varligi,
                           maker=update.maker, when_ms=update.zaman_ms, source=source)
        if status != leg.durum and status in ("EXPIRED", "REJECTED") and leg.rol == ROLE_STOP:
            self._note(f"{leg.istemci_kimligi} stop emri {status} ({reason or 'sebep yok'})")
        return updated

    def _add_fill(self, leg: DemoLeg, *, trade_id: int, price: Decimal, quantity: Decimal,
                  quote_qty: Decimal, commission: Decimal, asset: str | None, maker: bool,
                  when_ms: int, source: str) -> bool:
        position = self.ledger.position(leg.pozisyon_id)
        if position is None:
            return False
        return self.ledger.add_fill({
            "pozisyon_id": leg.pozisyon_id,
            "emir_istemci": leg.istemci_kimligi,
            "sembol": position.sembol,
            "taraf": leg.taraf,
            "islem_kimligi": int(trade_id),
            "fiyat": _m(price),
            "miktar": _m(quantity),
            "quote_miktar": _m(quote_qty),
            "komisyon": _m(commission),
            "komisyon_varligi": asset,
            "maker": 1 if maker else 0,
            "zaman_ms": int(when_ms or self._now_ms()),
            "kaynak": source,
        })

    def _apply_response(self, payload: dict[str, Any], *, source: str) -> None:
        """Emir/iptal yanıtındaki emir raporları ve (varsa) dolumlar."""
        reports = payload.get("orderReports")
        if isinstance(reports, list):
            for item in reports:
                if isinstance(item, dict):
                    self._apply_update(OrderUpdate.from_rest(item), source=source)
            return
        if "clientOrderId" in payload or "origClientOrderId" in payload:
            update = OrderUpdate.from_rest(payload)
            leg = self._apply_update(update, source=source)
            if leg is None:
                return
            for item in payload.get("fills", ()) or ():
                try:
                    price = Decimal(str(item["price"]))
                    quantity = Decimal(str(item["qty"]))
                    self._add_fill(leg, trade_id=int(item["tradeId"]), price=price,
                                   quantity=quantity, quote_qty=price * quantity,
                                   commission=Decimal(str(item.get("commission", "0"))),
                                   asset=item.get("commissionAsset"), maker=False,
                                   when_ms=int(payload.get("transactTime") or 0),
                                   source=source)
                except (KeyError, ValueError, ArithmeticError):
                    continue

    def _query_leg(self, position: DemoPosition, leg: DemoLeg) -> None:
        trader = self.trader
        if trader is None:
            return
        try:
            data = trader.query_order(position.sembol, leg.istemci_kimligi)
        except ExchangeError as error:
            if error.code == -2013:
                sent = leg.gonderim_ms or 0
                if leg.belirsiz or not leg.canli:
                    if self._server_ms() > sent + UNKNOWN_GRACE_MS:
                        self.ledger.update_leg(leg.istemci_kimligi, {
                            "durum": LEG_NEVER, "sebep": "borsada bulunamadı (hiç ulaşmadı)"})
                        self._note(f"{leg.istemci_kimligi} borsaya ulaşmamış; yeniden "
                                   "gönderilmedi")
                else:
                    self.ledger.update_leg(leg.istemci_kimligi, {
                        "durum": LEG_NEVER, "sebep": "borsada bulunamadı"})
                return
            raise
        self._apply_update(OrderUpdate.from_rest(data), source="sorgu")

    def _fetch_trades(self, position: DemoPosition, leg: DemoLeg) -> None:
        trader = self.trader
        if trader is None or leg.borsa_emir_kimligi is None:
            return
        mono = self.monotonic()
        if mono - self._trades_asked.get(leg.istemci_kimligi, -1e18) < TRADES_REFRESH_SECONDS:
            return
        self._trades_asked[leg.istemci_kimligi] = mono
        for item in trader.my_trades(position.sembol, leg.borsa_emir_kimligi):
            try:
                self._add_fill(
                    leg, trade_id=int(item["id"]), price=Decimal(str(item["price"])),
                    quantity=Decimal(str(item["qty"])),
                    quote_qty=Decimal(str(item.get("quoteQty")
                                          or Decimal(str(item["price"]))
                                          * Decimal(str(item["qty"])))),
                    commission=Decimal(str(item.get("commission", "0"))),
                    asset=item.get("commissionAsset"), maker=bool(item.get("isMaker")),
                    when_ms=int(item.get("time") or 0), source="rest",
                )
            except (KeyError, ValueError, ArithmeticError):
                continue

    # --- uzlaştırma ------------------------------------------------------------------------

    def reconcile(self, reason: str, *, full: bool = True) -> str:
        """Kayıtları borsayla karşılaştırır; borsanın söylediği esastır."""
        trader = self.trader
        if trader is None:
            return "Demo anahtarı yok; uzlaştırma yapılmadı."
        with self.lock:
            self._drain()
            mono = self.monotonic()
            self._last_reconcile = mono
            open_legs = self.ledger.open_legs()
            positions = {item.id: item for item in self.ledger.active()}
            symbols = set(self.symbols) if full else {
                position.sembol for position in positions.values()}
            warnings: list[str] = []
            seen: set[str] = set()
            manual: dict[str, int] = {}
            failed = False
            orphans = 0
            for symbol in sorted(symbols):
                try:
                    orders = trader.open_orders(symbol)
                except Exception as error:  # noqa: BLE001 - biri başarısızsa ötekiler sürer
                    failed = True
                    warnings.append(f"{symbol} açık emirleri alınamadı: "
                                    f"{errors.classify(error).mesaj_tr}")
                    continue
                for data in orders:
                    client = str(data.get("clientOrderId") or "")
                    if not ids.is_ours(client):
                        manual[symbol] = manual.get(symbol, 0) + 1
                        continue
                    seen.add(client)
                    if self.ledger.leg(client) is not None:
                        self._apply_update(OrderUpdate.from_rest(data), source="uzlastirma")
                        continue
                    orphans += 1
                    side = str(data.get("side") or "")
                    if side == "BUY":
                        try:
                            trader.cancel_order(symbol, client)
                            text = (f"Kayıtta olmayan albsat alış emri iptal edildi: {symbol} "
                                    f"{data.get('origQty')} @ {data.get('price')}")
                        except Exception as error:  # noqa: BLE001
                            text = (f"Kayıtta olmayan albsat alış emri iptal edilemedi: {client}: "
                                    f"{errors.classify(error).mesaj_tr}")
                    else:
                        text = (f"Kayıtta olmayan albsat satış emri borsada duruyor: {symbol} "
                                f"{data.get('origQty')} @ {data.get('price')} (dokunulmadı; "
                                "bir coini koruyor olabilir)")
                    warnings.append(text)
                    self.audit.write("demo_yetim_emir", text, kaynak=SOURCE_SYSTEM)
            for leg in open_legs:
                if leg.istemci_kimligi in seen:
                    continue
                position = positions.get(leg.pozisyon_id) or self.ledger.position(leg.pozisyon_id)
                if position is None or (not full and position.sembol not in symbols):
                    continue
                try:
                    self._query_leg(position, leg)
                except Exception as error:  # noqa: BLE001
                    failed = True
                    warnings.append(f"{leg.istemci_kimligi} sorgulanamadı: "
                                    f"{errors.classify(error).mesaj_tr}")
            for position in self.ledger.active():
                for leg in self.ledger.legs(position.id):
                    if leg.dec("dolan") > self.ledger.filled_for_leg(leg.istemci_kimligi):
                        try:
                            self._trades_asked.pop(leg.istemci_kimligi, None)
                            self._fetch_trades(position, leg)
                        except Exception as error:  # noqa: BLE001
                            failed = True
                            warnings.append(f"{leg.istemci_kimligi} dolumları alınamadı: "
                                            f"{errors.classify(error).mesaj_tr}")
            if full:
                self._last_full_reconcile = mono
                try:
                    self._refresh_account()
                    warnings.extend(self._balance_warnings())
                except Exception as error:  # noqa: BLE001
                    failed = True
                    warnings.append(f"Demo bakiyesi alınamadı: {errors.classify(error).mesaj_tr}")
                self.health.elle_emirler = manual
            if not failed:
                self._reconciled = True
            active = len(self.ledger.active())
            parts = [f"{active} açık Demo pozisyonu"]
            if manual:
                parts.append("elle verilmiş emirler (dokunulmadı): " + ", ".join(
                    f"{symbol} {count}" for symbol, count in sorted(manual.items())))
            if orphans:
                parts.append(f"{orphans} kayıtsız albsat emri")
            summary = f"Demo uzlaştırma ({reason}): " + "; ".join(parts) + "."
            if failed:
                summary += " Bazı sorgular başarısız; yeniden denenecek."
            self.health.son_uzlastirma_utc = iso(self.clock())
            self.health.son_uzlastirma_ozet = summary
            self.health.uyarilar = warnings
            if full or warnings:
                self._note(summary)
            for position in self.ledger.active():
                self._cooldown.pop(position.id, None)
            return summary

    def _balance_warnings(self) -> list[str]:
        warnings: list[str] = []
        account = self.account()
        for symbol in self.symbols:
            rules = self.rules_for(symbol)
            base = self._base_asset(symbol)
            held = account.coinler.get(symbol, ZERO)
            if held <= ZERO:
                continue
            free, locked = self._balances.get(base, (ZERO, ZERO))
            step = rules.lot.step_size if rules is not None and rules.lot else ZERO
            if free + locked + step < held:
                warnings.append(
                    f"Demo hesabında {base} bakiyesi ({_m(free + locked)}) bot kaydının "
                    f"({_m(held)}) altında. Coin elle satılmış ya da Demo bakiyesi sıfırlanmış "
                    "olabilir; bot yalnızca borsada olanı satabilir."
                )
        usdt = self._free("USDT")
        if usdt is not None and usdt < account.serbest_usdt:
            warnings.append(
                f"Demo hesabındaki serbest USDT ({_usdt(usdt)}) bot bütçesinin serbest kısmından "
                f"({_usdt(account.serbest_usdt)}) az; emir büyüklüğü borsadaki bakiyeyle "
                "sınırlanıyor."
            )
        return warnings

    # --- hesap ve risk görüntüsü -------------------------------------------------------------

    def period(self) -> tuple[int, Decimal, str]:
        return self.ledger.current_period(default_start_usdt=self.engine.limits.butce_usdt)

    def account(self, marks: dict[str, Decimal] | None = None) -> DemoAccountView:
        marks = marks or {}
        period_id, start, started = self.period()
        cash = start
        locked = ZERO
        coins: dict[str, Decimal] = {}
        for position in self.ledger.in_period(period_id):
            fills = self.ledger.fills(position.id)
            econ = self._econ(position, fills)
            cash += econ.satis_quote - econ.alis_quote - econ.komisyon_usdt
            coins[position.sembol] = coins.get(position.sembol, ZERO) + econ.alinan \
                - econ.satilan
            if position.bitti:
                continue
            for leg in self.ledger.legs(position.id):
                if leg.rol == ROLE_ENTRY and (leg.canli or leg.belirsiz):
                    locked += (leg.dec("miktar") - leg.dec("dolan")) * leg.dec("fiyat")
        coins = {key: value for key, value in coins.items() if value != ZERO}
        missing = tuple(sorted(key for key in coins if key not in marks))
        equity: Decimal | None = None
        change: Decimal | None = None
        if not missing:
            equity = cash + sum((value * marks[key] for key, value in coins.items()), ZERO)
            change = (equity - start) / start * ONE_HUNDRED if start > ZERO else None
        return DemoAccountView(
            donem_id=period_id, baslangic_usdt=start, baslangic_utc=started, nakit_usdt=cash,
            kilitli_usdt=locked, serbest_usdt=cash - locked, coinler=coins,
            ozsermaye_usdt=equity, getiri_yuzde=change, eksik_fiyat=missing,
            borsa_serbest_usdt=self._free("USDT"),
        )

    def bot_dust(self, symbol: str) -> Decimal:
        """Bu coinde botun, açık bir pozisyona ayrılmamış küsuratı."""
        period_id, _, _ = self.period()
        total = ZERO
        for position in self.ledger.in_period(period_id):
            if position.sembol != symbol:
                continue
            econ = self._econ(position, self.ledger.fills(position.id))
            if position.bitti:
                total += econ.alinan - econ.satilan
            else:
                total -= position.dec("onceki_toz")
        total = max(total, ZERO)
        base = self._base_asset(symbol)
        free = self._free(base)
        return min(total, free) if free is not None else total

    def snapshot(self, now: datetime) -> risk.AccountSnapshot:
        period_id, start, _ = self.period()
        positions = self.ledger.in_period(period_id)
        today = day_start(now)
        account = self.account()
        free = account.serbest_usdt
        exchange_free = self._free("USDT")
        if exchange_free is not None:
            free = min(free, exchange_free)
        active: list[risk.OpenExposure] = []
        for position in positions:
            if position.bitti:
                continue
            fills = self.ledger.fills(position.id)
            active.append(risk.OpenExposure(
                sembol=position.sembol, tutar_usdt=position.dec("tutar_usdt"),
                stop_zarari_usdt=position.dec("stop_zarari_usdt"), bekleyen=not fills))
        return risk.AccountSnapshot(
            baslangic_usdt=start,
            serbest_usdt=free,
            kapanan=tuple(
                risk.ClosedTrade(
                    sembol=item.sembol, periyot=item.periyot, kapanis_utc=item.kapanis or now,
                    net_usdt=item.dec("net_usdt"), kaynak=item.kaynak,
                    kural_kimligi=item.kural_kimligi, net_yuzde=float(item.net_yuzde or 0.0))
                for item in self.ledger.closed_in_period(period_id)
            ),
            acik=tuple(active),
            girisler_bugun=sum(
                1 for item in positions
                if item.durum != POS_REJECTED
                and (parse_utc(item.olusturma_utc) or today) >= today
            ),
            art_arda_sifirlama_utc=parse_utc(self.ledger.get_state(STATE_STREAK_RESET)),
        )

    # --- emir öncesi kapılar ------------------------------------------------------------------

    def demo_quote(self, symbol: str, *, fresh: bool = False) -> Quote | None:
        """Demo defterinin en iyi alış/satışı: akıştan, bayatsa REST'ten.

        ``fresh``: akıştaki fiyata güvenilmez, REST'ten okunur (borsa "hemen
        eşleşir" dediyse elimizdeki fiyat geride kalmıştır)."""
        quote = self.demo_market.quote(symbol)
        now = self.clock()
        if not fresh and quote is not None and \
                (now - quote.zaman).total_seconds() <= QUOTE_MAX_AGE_SECONDS:
            return quote
        mono = self.monotonic()
        if self.public is not None and (fresh or mono - self._last_quote_poll.get(
                symbol, -1e18) >= QUOTE_REST_EVERY_SECONDS):
            self._last_quote_poll[symbol] = mono
            try:
                for item in self.public.book_tickers([symbol]):
                    self.demo_market.on_book(str(item["symbol"]),
                                             Decimal(str(item["bidPrice"])),
                                             Decimal(str(item["askPrice"])))
            except Exception as error:  # noqa: BLE001 - fiyat yoksa kapı kapalı kalır
                self.health.son_hata = f"Demo fiyatı alınamadı: {error}"[:300]
            quote = self.demo_market.quote(symbol)
            if quote is not None and (now - quote.zaman).total_seconds() <= QUOTE_MAX_AGE_SECONDS:
                return quote
        return None

    def _order_limits(self) -> tuple[int | None, int | None]:
        snapshot = self.info_store.read() or self.live_info_store.read()
        short: int | None = None
        day: int | None = None
        if snapshot is not None:
            for item in snapshot.ham.get("rateLimits", ()) or ():
                if not isinstance(item, dict) or item.get("rateLimitType") != "ORDERS":
                    continue
                if item.get("interval") == "SECOND":
                    short = int(item.get("limit", 0)) or None
                elif item.get("interval") == "DAY":
                    day = int(item.get("limit", 0)) or None
        return short, day

    def demo_gates(self, intent: risk.OrderIntent, *, quantity_usdt: Decimal | None) -> list[Gate]:
        gates: list[Gate] = []
        reason = self.not_ready_reason()
        gates.append(Gate("demo_baglanti", "Demo bağlantısı", reason is None,
                          reason or "Demo hesabı bağlı, akış açık, uzlaştırma tamam."))
        unresolved = [leg for leg in self.ledger.open_legs() if leg.belirsiz]
        gates.append(Gate(
            "belirsiz_emir", "Sonucu bilinmeyen emir", not unresolved,
            "Sonucu bilinmeyen emir yok." if not unresolved else
            f"{len(unresolved)} emrin sonucu sorgulanıyor; netleşmeden yeni emir açılmaz."))
        rules = self.rules_for(intent.sembol)
        if rules is None:
            gates.append(Gate("demo_filtre", "Borsa filtreleri", False,
                              "Borsa filtreleri yok; emir yuvarlanamaz.", olculemedi=True))
        else:
            ok = rules.tradable and rules.otoco_allowed
            gates.append(Gate(
                "demo_filtre", "Borsa filtreleri", ok,
                f"{intent.sembol} işlemde, OTOCO destekli." if ok else
                f"{intent.sembol} şu an OTOCO emri alamıyor (durum {rules.status}, "
                f"OTO {'var' if rules.oto_allowed else 'yok'}, OCO "
                f"{'var' if rules.oco_allowed else 'yok'})."))
        short, day = self._order_limits()
        counts = self.trader.order_counts() if self.trader is not None else None
        order_ok = True
        text = "Emir sayısı sınırın çok altında."
        if counts is not None and counts.zaman is not None:
            fresh = self.monotonic() - counts.zaman <= 10
            if fresh and short and counts.son_10sn is not None and \
                    counts.son_10sn >= short * ORDER_SOFT_CAP_SHORT:
                order_ok = False
                text = (f"Son 10 saniyede {counts.son_10sn} emir; sınır {short}. Yeni giriş "
                        "biraz sonra.")
            if day and counts.son_1gun is not None and counts.son_1gun >= day * ORDER_SOFT_CAP_DAY:
                order_ok = False
                text = f"Bugün {counts.son_1gun} emir; günlük sınır {day}. Yeni giriş yok."
        gates.append(Gate("emir_sayaci", "Emir sayısı sınırı", order_ok, text))
        quote = self.demo_quote(intent.sembol)
        if quote is None:
            gates.append(Gate("limit_maker", "Limit-maker kuralı (Demo defteri)", False,
                              "Demo defterinin fiyatı alınamadı.", olculemedi=True))
        else:
            gate = post_only_gate(intent.giris, quote.satis)
            gates.append(Gate(gate.ad, "Limit-maker kuralı (Demo defteri)", gate.gecti,
                              gate.aciklama + " (Demo Mode'un defteri canlıdan ayrıdır.)"))
        if quantity_usdt is not None:
            free = self._free("USDT")
            gates.append(Gate(
                "demo_bakiye", "Demo hesabı bakiyesi",
                free is not None and free >= quantity_usdt,
                f"Demo hesabında serbest {_usdt(free)} USDT, emir {_usdt(quantity_usdt)} USDT."
                if free is not None else "Demo hesabının bakiyesi henüz okunmadı.",
                olculemedi=free is None))
        return gates

    def evaluate(self, intent: risk.OrderIntent, *, market: MarketState,
                 now: datetime) -> risk.RiskDecision:
        to_target, to_stop = self.round_trips(intent.sembol)
        mode = self.engine.modes.get(intent.sembol)
        decision = risk.evaluate(
            intent,
            snapshot=self.snapshot(now),
            limits=self.engine.limits,
            market=market,
            now=now,
            round_trip_to_target=to_target,
            round_trip_to_stop=to_stop,
            exit_slippage_pct=self.costs_for(intent.sembol).kayma_yuzde,
            mode_open=mode == MODE_DEMO,
            mode_text=(
                f"{intent.sembol} Demo Mode'da."
                if mode == MODE_DEMO
                else f"{intent.sembol} şu an '{MODE_LABELS_TR.get(mode, mode)}' modunda; Demo "
                "emri için Demo Mode'a alın."
            ),
            disabled_rules=self.engine.disabled_rules(),
        )
        size = decision.pozisyon
        gates = self.demo_gates(intent, quantity_usdt=size.tutar_usdt if size else None)
        return risk.RiskDecision(
            izin=decision.izin and all(gate.gecti for gate in gates),
            kapilar=(*decision.kapilar, *gates),
            pozisyon=size,
            hedef_net_yuzde=decision.hedef_net_yuzde,
        )

    def preview(self, intent: risk.OrderIntent, *, market: MarketState,
                now: datetime) -> PlaceResult:
        """Emir gönderilmeden: risk kapıları ve borsaya gidecek emirler."""
        with self.lock:
            decision = self.evaluate(intent, market=market, now=now)
            plan = self._plan(intent, decision, token="onizleme0000", attempt=1)
            message = decision.ozet_tr
            if plan is not None and not plan.gecerli:
                message += " Emir filtreleri: " + " ".join(plan.sorunlar)
            return PlaceResult(decision, None, message, plan)

    def _plan(self, intent: risk.OrderIntent, decision: risk.RiskDecision, *, token: str,
              attempt: int) -> Plan | None:
        rules = self.rules_for(intent.sembol)
        if rules is None or decision.pozisyon is None:
            return None
        return plan_otoco(
            rules=rules, token=token, attempt=attempt, entry=intent.giris, target=intent.hedef,
            stop=intent.stop, quantity=decision.pozisyon.miktar,
            fee_rate=self.buy_fee_rate(intent.sembol), carried_dust=self.bot_dust(intent.sembol),
            settings=self.settings,
        )

    # --- emir açma --------------------------------------------------------------------------

    def place(
        self,
        intent: risk.OrderIntent,
        *,
        market: MarketState,
        now: datetime,
        meta: OrderMeta | None = None,
        source: str = SOURCE_LOOP,
        reprice: bool = False,
    ) -> PlaceResult:
        """Risk kapılarından ve Demo kapılarından geçerse OTOCO gönderir."""
        meta = meta or OrderMeta()
        with self.lock:
            decision = self.evaluate(intent, market=market, now=now)
            label = meta.kural_etiketi or ("elle emir" if intent.kaynak == risk.SOURCE_MANUAL
                                           else intent.kural_kimligi or "")
            if not decision.izin or decision.pozisyon is None:
                self.audit.write("demo_emir_reddedildi", f"{intent.sembol} {label}: "
                                 f"{decision.ozet_tr}", kaynak=SOURCE_RISK,
                                 ayrinti={"sembol": intent.sembol, "kapali": ", ".join(
                                     item.ad for item in decision.kapali_kapilar)}, now=now)
                return PlaceResult(decision, None, decision.ozet_tr)
            token = ids.new_token()
            plan = self._plan(intent, decision, token=token, attempt=1)
            if plan is None or not plan.gecerli:
                text = "Emir borsa filtrelerine uymuyor: " + " ".join(
                    plan.sorunlar if plan else ("filtreler yok",))
                self.audit.write("demo_emir_reddedildi", f"{intent.sembol} {label}: {text}",
                                 kaynak=SOURCE_RISK, now=now)
                return PlaceResult(decision, None, text, plan)
            size = decision.pozisyon
            period_id, _, _ = self.period()
            step = interval_ms(intent.periyot)
            now_ms = to_ms(now)
            dust = self.bot_dust(intent.sembol)
            position = self.ledger.insert_position({
                "donem_id": period_id,
                "jeton": token,
                "olusturma_utc": iso(now),
                "sembol": intent.sembol,
                "periyot": intent.periyot,
                "kaynak": intent.kaynak,
                "kural_kimligi": intent.kural_kimligi,
                "kural_etiketi": meta.kural_etiketi,
                "sinyal_mumu_utc": meta.sinyal_mumu_utc,
                "giris": plan.params["workingPrice"],
                "hedef": plan.params["pendingAbovePrice"],
                "stop": plan.params["pendingBelowStopPrice"],
                "stop_limit": plan.params.get("pendingBelowPrice"),
                "miktar": plan.params["workingQuantity"],
                "tutar_usdt": _m(size.tutar_usdt),
                "stop_zarari_usdt": _m(size.stop_zarari_usdt),
                "bekleyen_miktar": plan.params["pendingQuantity"],
                "onceki_toz": _m(dust),
                "gecerlilik_bitis_ms": now_ms + step * max(meta.gecerlilik_mum, 1),
                "azami_tutma_ms": step * max(meta.azami_tutma_mum, 1),
                "beklenen_hedef_net_yuzde": (
                    meta.beklenen_hedef_net_yuzde if meta.beklenen_hedef_net_yuzde is not None
                    else float(decision.hedef_net_yuzde or 0)),
                "beklenen_ortalama_yuzde": meta.beklenen_ortalama_yuzde,
                "yeniden_fiyatlama": meta.yeniden_fiyatlama,
                "durum": POS_SENDING,
                "notlar": _json_list(meta.notlar),
            }, [dict(leg, gonderim_ms=self._server_ms()) for leg in plan.legs])
            self.audit.write(
                "demo_emir",
                f"Demo OTOCO: {position.sembol} {position.miktar} @ {position.giris} (hedef "
                f"{position.hedef}, stop {position.stop}, "
                f"satış miktarı {position.bekleyen_miktar})",
                kaynak=source,
                ayrinti={"jeton": token, "kaynak_tur": intent.kaynak,
                         "kural": intent.kural_kimligi, "tutar_usdt": position.tutar_usdt,
                         "baglayici": size.baglayici.value},
                now=now,
            )
            info = self._send_otoco(position, plan)
            attempt = 1
            while (info is not None and info.yeniden_fiyatla and reprice
                   and attempt <= self.settings.yeniden_fiyatlama_denemesi):
                repriced = self._reprice(position, intent, market=market, now=now,
                                         attempt=attempt + 1)
                if repriced is None:
                    break
                position, plan = repriced
                attempt += 1
                info = self._send_otoco(position, plan)
            position = self.ledger.position(position.id) or position
            if info is None:
                self._notify(
                    f"📝 DEMO EMİR — {position.sembol} {position.periyot}\n"
                    f"Kaynak: {label}\n"
                    f"Giriş (limit-maker) {position.giris}, hedef {position.hedef}, stop "
                    f"{position.stop}\nMiktar {position.miktar} ≈ "
                    f"{_usdt(position.dec('tutar_usdt'))} USDT; stopta zarar ≈ "
                    f"{_usdt(position.dec('stop_zarari_usdt'))} USDT\n"
                    f"Geçerlilik: {istanbul_text(from_ms(position.gecerlilik_bitis_ms))}'a kadar.\n"
                    "Binance Demo Mode (sahte para).",
                    KIND_ORDER,
                )
                return PlaceResult(decision, position, "Demo emri borsaya gönderildi.", plan)
            if info.belirsiz:
                return PlaceResult(decision, position, info.mesaj_tr, plan)
            self._notify(f"❌ DEMO EMİR REDDEDİLDİ — {position.sembol}\n{info.mesaj_tr}",
                         KIND_ORDER)
            return PlaceResult(decision, position, info.mesaj_tr, plan)

    def _send_otoco(self, position: DemoPosition, plan: Plan) -> errors.ErrorInfo | None:
        """``None``: emir borsada. Aksi hâlde hata bilgisi (pozisyon güncellenmiş olur)."""
        assert self.trader is not None
        try:
            response = self.trader.place_otoco(plan.params)
        except Exception as error:  # noqa: BLE001 - her hata sınıflandırılır
            info = errors.classify(error)
            clients = [str(leg["istemci_kimligi"]) for leg in plan.legs]
            if info.belirsiz:
                sent = getattr(error, "sent_ms", None)
                for client in clients:
                    changes: dict[str, Any] = {"durum": LEG_UNKNOWN, "sebep": info.mesaj_tr}
                    if sent is not None:
                        changes["gonderim_ms"] = int(sent)
                    self.ledger.update_leg(client, changes)
                self.ledger.update_position(position.id, {"durum": POS_UNKNOWN,
                                                          "aciklama": info.mesaj_tr})
                self._note(f"{position.sembol} #{position.id}: {info.mesaj_tr}")
                return info
            for client in clients:
                self.ledger.update_leg(client, {"durum": LEG_NEVER, "sebep": info.mesaj_tr})
            self.ledger.update_position(position.id, {"durum": POS_REJECTED,
                                                      "aciklama": info.mesaj_tr,
                                                      "kapanis_utc": iso(self.clock())})
            self.audit.write("demo_emir_reddedildi",
                             f"{position.sembol} #{position.id}: {info.mesaj_tr}",
                             kaynak=SOURCE_SYSTEM, ayrinti={"kod": info.kod,
                                                            "kategori": info.kategori})
            return info
        self._apply_response(response, source="yanit")
        self.ledger.update_position(position.id, {"durum": POS_PENDING, "aciklama": None})
        return None

    def _reprice(self, position: DemoPosition, intent: risk.OrderIntent, *, market: MarketState,
                 now: datetime, attempt: int) -> tuple[DemoPosition, Plan] | None:
        """Limit-maker giriş hemen eşleşeceği için reddedildi: en iyi alışa çekip yeniden."""
        quote = self.demo_quote(intent.sembol, fresh=True)
        rules = self.rules_for(intent.sembol)
        if quote is None or rules is None:
            return None
        entry = rules.round_price(quote.alis, Side.BUY)
        if entry <= intent.stop or entry >= quote.satis:
            return None
        new_intent = risk.OrderIntent(sembol=intent.sembol, periyot=intent.periyot, giris=entry,
                                      hedef=intent.hedef, stop=intent.stop, kaynak=intent.kaynak,
                                      kural_kimligi=intent.kural_kimligi)
        decision = self.evaluate(new_intent, market=market, now=now)
        if not decision.izin or decision.pozisyon is None:
            self._note(f"{intent.sembol} yeniden fiyatlama risk kapısına takıldı: "
                       f"{decision.ozet_tr}")
            return None
        plan = self._plan(new_intent, decision, token=position.jeton, attempt=attempt)
        if plan is None or not plan.gecerli:
            return None
        notes = position.not_listesi + [
            f"Giriş {position.giris} Demo defterinde hemen eşleşeceği için reddedildi; en iyi "
            f"alışa ({entry}) çekilip yeniden gönderildi (deneme {attempt})."]
        size = decision.pozisyon
        updated = self.ledger.update_position(position.id, {
            "giris": plan.params["workingPrice"],
            "miktar": plan.params["workingQuantity"],
            "tutar_usdt": _m(size.tutar_usdt),
            "stop_zarari_usdt": _m(size.stop_zarari_usdt),
            "bekleyen_miktar": plan.params["pendingQuantity"],
            "yeniden_fiyatlama": position.yeniden_fiyatlama + 1,
            "durum": POS_SENDING,
            "aciklama": None,
            "kapanis_utc": None,
            "notlar": _json_list(notes),
        })
        self.ledger.add_legs(position.id, [dict(leg, gonderim_ms=self._server_ms())
                                           for leg in plan.legs])
        return updated, plan

    def place_from_card(self, card: Any, rule: Any) -> PlaceResult | None:
        """Canlı döngüden: coin Demo Mode'dayken yeni AL sinyali."""
        now = self.clock()
        signal_close = parse_utc(card.sinyal_mumu_kapanis_utc)
        if signal_close is not None and (now - signal_close).total_seconds() > \
                SIGNAL_MAX_AGE_SECONDS:
            self._note(f"{card.sembol} {card.kural_etiketi}: sinyal mumu eski, Demo emri açılmadı")
            return None
        entry = card.giris
        notes: list[str] = []
        quote = self.demo_quote(card.sembol)
        rules = self.rules_for(card.sembol)
        if quote is not None and entry >= quote.satis and rules is not None:
            entry = rules.round_price(quote.alis, Side.BUY)
            notes.append(f"Giriş {card.giris} Demo defterindeki en iyi satışın ({quote.satis}) "
                         f"üstünde kaldığı için en iyi alışa ({entry}) yazıldı.")
        intent = risk.OrderIntent(sembol=card.sembol, periyot=card.periyot, giris=entry,
                                  hedef=card.hedef1, stop=card.stop, kaynak=risk.SOURCE_RULE,
                                  kural_kimligi=card.kural_kimligi)
        meta = OrderMeta(
            kural_etiketi=card.kural_etiketi,
            sinyal_mumu_utc=card.sinyal_mumu_kapanis_utc,
            gecerlilik_mum=card.gecerlilik_mum,
            azami_tutma_mum=rule.pencere_mum,
            beklenen_hedef_net_yuzde=float(card.net_marj_yuzde),
            beklenen_ortalama_yuzde=float(rule.kanit.test_donemi_net_yuzde),
            yeniden_fiyatlama=1 if notes else 0,
            notlar=tuple(notes),
        )
        result = self.place(intent, market=self.live_market.market_state(
            card.sembol, card.periyot, rules), now=now, meta=meta, source=SOURCE_LOOP,
            reprice=True)
        if result.pozisyon is None:
            self._notify(f"ℹ️ {card.sembol} sinyali için Demo emri açılmadı.\n{result.mesaj}",
                         KIND_SIGNAL)
        return result

    # --- kullanıcı istekleri ------------------------------------------------------------------

    def request_cancel(self, position_id: int, *, source: str) -> DemoPosition:
        with self.lock:
            position = self.ledger.position(position_id)
            if position is None or position.bitti:
                raise ValueError("Bu Demo pozisyonu yok ya da bitmiş.")
            legs = self.ledger.legs(position.id)
            if not any(leg.rol == ROLE_ENTRY and (leg.canli or leg.belirsiz) for leg in legs):
                raise ValueError("Bu pozisyonun bekleyen girişi yok; kapatmak için 'Kapat'.")
            updated = self.ledger.update_position(position.id, {
                "giris_iptal_istegi": "Kullanıcı girişi iptal etti."})
            self.audit.write("demo_iptal_istegi", f"{position.sembol} #{position.id} girişi "
                             "iptal ediliyor", kaynak=source)
            self._cooldown.pop(position.id, None)
            self._advance(updated)
            return self.ledger.position(position.id) or updated

    def request_close(self, position_id: int, *, source: str,
                      reason: str = EXIT_MANUAL) -> DemoPosition:
        with self.lock:
            position = self.ledger.position(position_id)
            if position is None or position.bitti:
                raise ValueError("Bu Demo pozisyonu yok ya da bitmiş.")
            updated = self.ledger.update_position(position.id, {
                "cikis_istegi": reason,
                "aciklama": f"Kapatılıyor: {EXIT_LABELS_TR.get(reason, reason)}."})
            self.audit.write("demo_kapatma_istegi", f"{position.sembol} #{position.id} "
                             f"kapatılıyor ({EXIT_LABELS_TR.get(reason, reason)})",
                             kaynak=source)
            self._cooldown.pop(position.id, None)
            self._advance(updated)
            return self.ledger.position(position.id) or updated

    def kill_switch(self, *, close_positions: bool, source: str) -> int:
        """ACİL DURDUR'un Demo kısmı. Kâğıt motorunun ``kill_switch``'i modları zaten
        indirmiş ve bu yürütücüye bekleyen girişleri iptal isteği bırakmıştır."""
        with self.lock:
            self._apply_halts()
            count = 0
            if close_positions:
                for position in self.ledger.active():
                    self.ledger.update_position(position.id, {
                        "cikis_istegi": EXIT_KILL, "aciklama": "ACİL DURDUR: kapatılıyor."})
                    count += 1
            self.audit.write("demo_acil_durdur", "Demo acil durdurma çalıştı", kaynak=source,
                             ayrinti={"kapatilan": count, "pozisyonlar_kapatildi": close_positions})
            if self.trader is not None and self.health.hazirlik_tamam:
                for position in self.ledger.active():
                    self._cooldown.pop(position.id, None)
                    with contextlib.suppress(Exception):
                        self._advance(position)
            return count

    def reset_streak(self, *, source: str, now: datetime) -> None:
        with self.lock:
            self.ledger.set_state(STATE_STREAK_RESET, iso(now))
            self.audit.write("demo_art_arda_sifirlama", "Demo art arda kayıp sayacı sıfırlandı",
                             kaynak=source, now=now)

    def reset_account(self, *, source: str, now: datetime) -> int:
        with self.lock:
            if self.ledger.active():
                raise ValueError("Açık Demo pozisyonu ya da bekleyen emir varken Demo hesap "
                                 "dönemi sıfırlanamaz.")
            period_id = self.ledger.reset_period(start_usdt=self.engine.limits.butce_usdt,
                                                 now=now)
            self.ledger.set_json(STATE_NOTIFIED_BREACHES, {})
            self.audit.write("demo_hesap_sifirlama", "Demo bot bütçesi yeni dönemle başladı",
                             kaynak=source, now=now)
            return period_id

    # --- pozisyon adımı -------------------------------------------------------------------------

    def _set(self, position: DemoPosition, state: str, text: str | None = None) -> DemoPosition:
        changes: dict[str, Any] = {}
        if position.durum != state:
            changes["durum"] = state
        if text is not None and position.aciklama != text:
            changes["aciklama"] = text
        return self.ledger.update_position(position.id, changes) if changes else position

    def _signature(self, position_id: int) -> tuple[Any, ...]:
        position = self.ledger.position(position_id)
        if position is None:
            return ()
        legs = tuple((leg.istemci_kimligi, leg.durum, leg.dolan)
                     for leg in self.ledger.legs(position_id))
        return (position.durum, position.cikis_istegi, position.koruma_denemesi,
                position.cikis_denemesi, legs, len(self.ledger.fills(position_id)))

    def _advance(self, position: DemoPosition) -> None:
        """Bir pozisyonu, ilerleme durana kadar (en fazla birkaç adım) yürütür.

        Korumasız geçen her saniye önemli: iptal yanıtı geldiyse koruma aynı turda
        kurulur, çıkış dolduysa pozisyon aynı turda kapanır."""
        for _ in range(4):
            before = self._signature(position.id)
            self._step(position)
            current = self.ledger.position(position.id)
            if current is None or current.bitti or self._signature(position.id) == before:
                return
            position = current

    def _step(self, position: DemoPosition) -> None:
        if position.bitti or self.trader is None:
            return
        if self._cooldown.get(position.id, -1e18) > self.monotonic():
            return
        view = self._view(position)
        if view.unresolved:
            if not view.fills:
                position = self._set(position, POS_UNKNOWN)
            for leg in view.unresolved:
                self._query_leg(position, leg)
            self._cool(position.id, 1.0)
            return
        missing = [leg for leg in view.legs
                   if leg.dec("dolan") > sum((fill.dec("miktar") for fill in view.fills
                                              if fill.emir_istemci == leg.istemci_kimligi), ZERO)]
        if missing:
            for leg in missing:
                self._fetch_trades(position, leg)
            view = self._view(self.ledger.position(position.id) or position)
        if view.econ.alinan_brut <= ZERO:
            self._step_unfilled(view)
        else:
            self._step_filled(view)

    def _step_unfilled(self, view: _View) -> None:
        position = view.position
        now_ms = self._now_ms()
        entry_live = view.live(ROLE_ENTRY)
        if entry_live:
            position = self._set(position, POS_PENDING)
            reason: str | None = None
            if position.cikis_istegi:
                reason = f"Kapatma istendi ({EXIT_LABELS_TR.get(position.cikis_istegi, '')})."
            elif position.giris_iptal_istegi:
                reason = position.giris_iptal_istegi
            elif now_ms >= position.gecerlilik_bitis_ms:
                reason = "Giriş fiyatı geçerlilik süresi içinde gelmedi."
            if reason is not None:
                self._cancel_legs(view, entry_live, reason)
            else:
                self._check_stale(position, entry_live, only_pending_cancel=True)
            return
        live = view.live()
        if live:
            self._cancel_legs(view, live, "Giriş bitti; bekleyen hedef/stop kaldırılıyor.")
            return
        entries = [leg for leg in view.legs if leg.rol == ROLE_ENTRY]
        last = entries[-1] if entries else None
        if last is not None and last.durum == LEG_NEVER:
            # Yanıtı alınamayan istek, recvWindow geçtikten sonra borsada yok.
            state = POS_REJECTED
            text = ("Emir borsaya ulaşmadı (yanıt alınamamıştı; bekleme süresinden sonra "
                    "borsada bulunamadı). Aynı emir yeniden gönderilmedi.")
        elif last is not None and last.durum == "REJECTED":
            state = POS_REJECTED
            text = f"Borsa emri reddetti ({last.sebep or 'sebep bildirilmedi'})."
        else:
            state = POS_CANCELLED
            text = (position.giris_iptal_istegi or position.aciklama
                    or (f"Giriş borsada bitti ({last.durum}, {last.sebep or 'sebep yok'})."
                        if last is not None else "Giriş emri yok."))
        self.ledger.update_position(position.id, {
            "durum": state, "aciklama": text, "kapanis_utc": iso(self.clock())})
        self.audit.write("demo_iptal", f"Demo giriş bitti: {position.sembol} #{position.id} — "
                         f"{text}", kaynak=SOURCE_LOOP, ayrinti={"jeton": position.jeton})
        self._notify(f"⏹ İPTAL (demo) — {position.sembol} giriş emri @ {position.giris}\n{text}",
                     KIND_CANCEL)

    def _step_filled(self, view: _View) -> None:
        position = view.position
        now_ms = self._now_ms()
        settings = self.settings
        if position.ilk_dolum_ms is None:
            first = min(fill.zaman_ms for fill in view.fills if fill.taraf == "BUY")
            position = self.ledger.update_position(position.id, {"ilk_dolum_ms": first})
            entry_leg = next((leg for leg in view.legs if leg.rol == ROLE_ENTRY
                              and leg.dec("dolan") > ZERO), None)
            full = entry_leg is not None and entry_leg.durum == "FILLED"
            self._notify(
                f"✅ {'DOLDU' if full else 'KISMEN DOLDU'} (demo) — {position.sembol}: "
                f"{_m(view.econ.alinan_brut)} / {position.miktar} @ "
                f"{_text(view.econ.ortalama_giris)}\nHedef {position.hedef}, stop {position.stop}."
                + ("" if full else f"\nKalan giriş en fazla {settings.korumasiz_azami_saniye} sn "
                   "bekletilecek; sonra dolan kısım için stop ve hedef kurulacak."),
                KIND_FILL,
            )
            view = self._view(position)
        rules = view.rules
        quote = self.demo_quote(position.sembol)
        price_hint = quote.alis if quote is not None else position.dec("stop")
        sellable = self._can_sell(rules, view.sellable, price_hint)
        exits = view.live(ROLE_EXIT)
        if exits:
            self._set(position, POS_EXITING)
            self._check_stale(position, exits)
            return
        if view.stop_live and not position.cikis_istegi:
            position = self._mark_protected(position, now_ms)
            self._set(position, POS_PROTECTED, "Stop ve hedef borsada.")
            if position.ilk_dolum_ms is not None and \
                    now_ms >= position.ilk_dolum_ms + position.azami_tutma_ms:
                self._request_exit(position, EXIT_TIME,
                                   "Azami tutma süresi doldu; pozisyon kapatılıyor.")
            return
        if position.cikis_istegi:
            live = view.live()
            if live:
                self._set(position, POS_EXITING)
                self._cancel_legs(view, live, "Pozisyon kapatılıyor.")
                return
            if not sellable:
                self._close(view, quote)
                return
            self._exit(view, quote)
            return
        if not sellable and not view.live():
            # Satılacak miktar kalmadı (hedef/stop doldu) ya da kalan, borsanın en küçük
            # emrinin altında: pozisyon kapanır, küsurat hesapta kalır.
            self._close(view, quote)
            return
        position = self._mark_unprotected(position, now_ms)
        view.position = position
        since = position.korumasiz_baslangic_ms or now_ms
        waited = now_ms - since
        entry_live = view.live(ROLE_ENTRY)
        partial_targets = [leg for leg in view.live(ROLE_TARGET)
                           if leg.durum == "PARTIALLY_FILLED"]
        if entry_live or partial_targets:
            working = entry_live or partial_targets
            self._set(position, POS_PARTIAL if entry_live else POS_UNPROTECTED,
                      f"Kısmi dolum; {waited // 1000} sn'dir korumasız.")
            reason: str | None = None
            if waited >= settings.korumasiz_azami_saniye * 1000:
                reason = (f"Kısmi dolumdan sonra {settings.korumasiz_azami_saniye} sn korumasız "
                          "kaldı; kalan emir iptal edilip dolan miktar korunuyor.")
            elif entry_live and position.giris_iptal_istegi:
                reason = position.giris_iptal_istegi
            elif entry_live and now_ms >= position.gecerlilik_bitis_ms:
                reason = "Giriş geçerlilik süresi doldu; dolan kısım korunuyor."
            if reason is not None:
                self._cancel_legs(view, working, reason)
            return
        pending = view.pending_new
        if pending:
            self._set(position, POS_UNPROTECTED, "Hedef ve stop borsaya konuluyor.")
            if waited >= PENDING_ACTIVATION_SECONDS * 1000:
                self._check_stale(position, pending)
            return
        live = view.live()
        if live:
            self._set(position, POS_UNPROTECTED, "Stop yok; kalan emir kaldırılıp koruma "
                      "yeniden kuruluyor.")
            self._cancel_legs(view, live, "Stop borsada değil; koruma yeniden kuruluyor.")
            return
        self._set(position, POS_UNPROTECTED)
        self._protect(view, quote)

    def _can_sell(self, rules: SymbolRules | None, quantity: Decimal, price: Decimal) -> bool:
        if quantity <= ZERO:
            return False
        if rules is None:
            return True
        if rules.lot is not None and quantity < rules.lot.min_qty:
            return False
        return not (rules.notional is not None and quantity * price < rules.notional.min_notional)

    def _check_stale(self, position: DemoPosition, legs: Sequence[DemoLeg], *,
                     only_pending_cancel: bool = False) -> None:
        """Uzun süre haber alınamayan emirleri sorgular (kopan akışa karşı)."""
        mono = self.monotonic()
        for leg in legs:
            if only_pending_cancel and leg.durum != "PENDING_CANCEL":
                continue
            seen = self._leg_seen.get(leg.istemci_kimligi)
            if seen is None:
                self._leg_seen[leg.istemci_kimligi] = mono
                continue
            if mono - seen >= QUERY_STALE_SECONDS:
                self._leg_seen[leg.istemci_kimligi] = mono
                self._query_leg(position, leg)

    def _mark_unprotected(self, position: DemoPosition, now_ms: int) -> DemoPosition:
        if position.korumasiz_baslangic_ms is None:
            return self.ledger.update_position(position.id, {"korumasiz_baslangic_ms": now_ms})
        if (now_ms - position.korumasiz_baslangic_ms >= PENDING_ACTIVATION_SECONDS * 1000
                and position.id not in self._unprotected_notified):
            self._unprotected_notified.add(position.id)
            self._notify(f"⚠️ KORUMASIZ (demo) — {position.sembol} #{position.id}\n"
                         "Elde coin var ama borsada stop yok. Uygulama stop kurmaya ya da "
                         "korumalı satmaya çalışıyor.", KIND_STOP)
        return position

    def _mark_protected(self, position: DemoPosition, now_ms: int) -> DemoPosition:
        start = position.korumasiz_baslangic_ms
        if start is None:
            return position
        duration = max(0, now_ms - start)
        self._unprotected_notified.discard(position.id)
        if duration >= 1000:
            self._note(f"{position.sembol} #{position.id} {duration / 1000:.1f} sn korumasız "
                       "kaldı; koruma kuruldu")
        return self.ledger.update_position(position.id, {
            "korumasiz_baslangic_ms": None,
            "korumasiz_toplam_ms": position.korumasiz_toplam_ms + duration,
            "korumasiz_azami_ms": max(position.korumasiz_azami_ms, duration),
        })

    def _request_exit(self, position: DemoPosition, reason: str, text: str) -> DemoPosition:
        if position.cikis_istegi:
            return position
        self.audit.write("demo_cikis_istegi", f"{position.sembol} #{position.id}: {text}",
                         kaynak=SOURCE_LOOP)
        updated = self.ledger.update_position(position.id, {"cikis_istegi": reason,
                                                            "aciklama": text})
        self._cooldown.pop(position.id, None)
        return updated

    def _cancel_legs(self, view: _View, legs: Sequence[DemoLeg], reason: str) -> None:
        trader = self.trader
        if trader is None:
            return
        position = view.position
        mono = self.monotonic()
        lists = sorted({leg.liste_istemci_kimligi for leg in legs if leg.liste_istemci_kimligi})
        singles = [leg for leg in legs if not leg.liste_istemci_kimligi]
        targets: list[tuple[str, str]] = [("list", item) for item in lists]
        targets += [("order", leg.istemci_kimligi) for leg in singles]
        for kind, client in targets:
            if mono - self._cancel_sent.get(client, -1e18) < QUERY_STALE_SECONDS:
                continue
            self._cancel_sent[client] = mono
            try:
                if kind == "list":
                    response = trader.cancel_order_list(position.sembol, client)
                else:
                    response = trader.cancel_order(position.sembol, client)
            except Exception as error:  # noqa: BLE001 - her hata sınıflandırılır
                info = errors.classify(error)
                if info.kategori == errors.ERR_UNKNOWN_ORDER:
                    for leg in legs:
                        if leg.liste_istemci_kimligi == client or leg.istemci_kimligi == client:
                            self._query_leg(position, leg)
                elif not info.belirsiz:
                    self._note(f"{position.sembol} #{position.id} iptal edilemedi: "
                               f"{info.mesaj_tr}")
                    self._cool(position.id, ERROR_COOLDOWN_SECONDS)
                continue
            self._apply_response(response, source="iptal_yaniti")
            self.audit.write("demo_iptal_emri", f"{position.sembol} #{position.id}: {reason}",
                             kaynak=SOURCE_LOOP, ayrinti={"kimlik": client})
        if position.aciklama != reason:
            self.ledger.update_position(position.id, {"aciklama": reason})

    def _protect(self, view: _View, quote: Quote | None) -> None:
        position = view.position
        rules = view.rules
        trader = self.trader
        if trader is None or rules is None:
            return
        if quote is None:
            self._note(f"{position.sembol} #{position.id}: Demo fiyatı yok; koruma "
                       "birazdan yeniden denenecek")
            self._cool(position.id, ACTION_COOLDOWN_SECONDS)
            return
        target, stop = position.dec("hedef"), position.dec("stop")
        if quote.alis <= stop:
            self._request_exit(position, EXIT_STOP, f"Fiyat ({quote.alis}) stopun ({stop}) "
                               "altında; stop kurulamaz, korumalı satışla çıkılıyor.")
            return
        if quote.alis >= target:
            self._request_exit(position, EXIT_TARGET, f"Fiyat ({quote.alis}) hedefte ya da "
                               f"üstünde ({target}); korumalı satışla çıkılıyor.")
            return
        attempt = position.koruma_denemesi + 1
        plan = plan_oco(rules=rules, token=position.jeton, attempt=attempt, target=target,
                        stop=stop, quantity=view.sellable, settings=self.settings)
        if not plan.gecerli:
            self._request_exit(position, EXIT_PROTECT, "Koruma emri filtrelere uymuyor ("
                               + " ".join(plan.sorunlar) + "); korumalı satışla çıkılıyor.")
            return
        self.ledger.add_legs(position.id, [dict(leg, gonderim_ms=self._server_ms())
                                           for leg in plan.legs])
        position = self.ledger.update_position(position.id, {"koruma_denemesi": attempt})
        try:
            response = trader.place_oco(plan.params)
        except Exception as error:  # noqa: BLE001 - her hata sınıflandırılır
            self._leg_failure(position, plan, error, action="koruma")
            return
        self._apply_response(response, source="yanit")
        self.audit.write("demo_koruma",
                         f"{position.sembol} #{position.id}: {plan.params['quantity']} için "
                         f"hedef {target} / stop {stop} kuruldu (deneme {attempt})",
                         kaynak=SOURCE_LOOP)
        self._notify(f"🛡 KORUMA KURULDU (demo) — {position.sembol} #{position.id}\n"
                     f"{plan.params['quantity']} için hedef {target}, stop {stop}.", KIND_STOP)

    def _exit(self, view: _View, quote: Quote | None) -> None:
        position = view.position
        rules = view.rules
        trader = self.trader
        if trader is None or rules is None:
            return
        if quote is None:
            self._note(f"{position.sembol} #{position.id}: Demo fiyatı yok; çıkış birazdan "
                       "yeniden denenecek")
            self._cool(position.id, ACTION_COOLDOWN_SECONDS)
            return
        attempt = position.cikis_denemesi + 1
        plan = plan_exit(rules=rules, token=position.jeton, attempt=attempt,
                         quantity=view.sellable, best_bid=quote.alis, settings=self.settings)
        if not plan.gecerli:
            self._note(f"{position.sembol} #{position.id} çıkış emri filtrelere uymuyor: "
                       + " ".join(plan.sorunlar))
            self._close(view, quote)
            return
        self.ledger.add_legs(position.id, [dict(leg, gonderim_ms=self._server_ms())
                                           for leg in plan.legs])
        position = self.ledger.update_position(position.id, {"cikis_denemesi": attempt,
                                                             "durum": POS_EXITING})
        try:
            response = trader.place_exit(plan.params)
        except Exception as error:  # noqa: BLE001 - her hata sınıflandırılır
            self._leg_failure(position, plan, error, action="çıkış")
            return
        self._apply_response(response, source="yanit")
        status = str(response.get("status") or "")
        if status != "FILLED":
            self._cool(position.id, 1.0)
            if attempt % EXIT_ALERT_ATTEMPTS == 0:
                self._notify(f"⚠️ ÇIKIŞ DOLMUYOR (demo) — {position.sembol} #{position.id}\n"
                             f"{attempt} denemedir korumalı satış tam dolmadı (en iyi alışın "
                             f"%{self.settings.azami_kayma_yuzde} altına kadar). Denemeye devam "
                             "ediliyor; Demo defterine bakın.", KIND_STOP)

    def _leg_failure(self, position: DemoPosition, plan: Plan, error: BaseException, *,
                     action: str) -> None:
        info = errors.classify(error)
        clients = [str(leg["istemci_kimligi"]) for leg in plan.legs]
        if info.belirsiz:
            sent = getattr(error, "sent_ms", None)
            for client in clients:
                changes: dict[str, Any] = {"durum": LEG_UNKNOWN, "sebep": info.mesaj_tr}
                if sent is not None:
                    changes["gonderim_ms"] = int(sent)
                self.ledger.update_leg(client, changes)
            self._note(f"{position.sembol} #{position.id} {action}: {info.mesaj_tr}")
            return
        for client in clients:
            self.ledger.update_leg(client, {"durum": LEG_NEVER, "sebep": info.mesaj_tr})
        self._note(f"{position.sembol} #{position.id} {action} reddedildi: {info.mesaj_tr}")
        if info.kategori in (errors.ERR_WOULD_TRIGGER, errors.ERR_OCO_PRICES,
                             errors.ERR_WOULD_MATCH) and action == "koruma":
            self._request_exit(position, EXIT_PROTECT, f"Koruma kurulamadı ({info.mesaj_tr}); "
                               "korumalı satışla çıkılıyor.")
            return
        if info.kategori == errors.ERR_BALANCE:
            # Coin başka bir emirde kilitli olabilir (ör. akışta kaçan bir OTOCO bacağı);
            # borsanın açık emirleri hemen okunur.
            self._reconcile_reason = f"{position.sembol} #{position.id}: yetersiz bakiye"
            with contextlib.suppress(Exception):
                self._refresh_account()
        self._cool(position.id, ERROR_COOLDOWN_SECONDS if not info.bekle_dene else
                   ACTION_COOLDOWN_SECONDS)

    def _close(self, view: _View, quote: Quote | None) -> None:
        position = view.position
        econ = view.econ
        exit_price = econ.ortalama_cikis
        if exit_price is None:
            exit_price = quote.alis if quote is not None else position.dec("stop")
        cost, net, pct = net_result(econ, exit_price=exit_price)
        sold_by_role: dict[str, Decimal] = {}
        roles = {leg.istemci_kimligi: leg.rol for leg in view.legs}
        for fill in view.fills:
            if fill.taraf == "SELL":
                role = roles.get(fill.emir_istemci, ROLE_EXIT)
                sold_by_role[role] = sold_by_role.get(role, ZERO) + fill.dec("miktar")
        if position.cikis_istegi in (EXIT_TIME, EXIT_KILL, EXIT_MANUAL):
            reason = position.cikis_istegi
        elif not sold_by_role:
            reason = EXIT_DUST
        else:
            main = max(sold_by_role, key=lambda key: sold_by_role[key])
            reason = {ROLE_TARGET: EXIT_TARGET, ROLE_STOP: EXIT_STOP}.get(
                main, position.cikis_istegi or EXIT_PROTECT)
        now_ms = self._now_ms()
        position = self._mark_protected(position, now_ms)
        fee_coin_usdt = econ.alis_komisyon_coin * (econ.ortalama_giris or ZERO)
        notes = position.not_listesi
        if econ.degerlenemeyen:
            notes.append("Komisyonun bir kısmı " + ", ".join(econ.degerlenemeyen)
                         + " ile ödendi ve fiyatı bilinmediği için sonuca katılmadı.")
        dust = econ.alinan - econ.satilan
        closed = self.ledger.update_position(position.id, {
            "durum": POS_CLOSED,
            "kapanis_utc": iso(self.clock()),
            "cikis_sebebi": reason,
            "cikis_fiyati": _m(exit_price),
            "maliyet_usdt": _m(cost),
            "gelir_usdt": _m(econ.satis_quote),
            "komisyon_usdt": _m(econ.komisyon_usdt + fee_coin_usdt),
            "toz_degisimi": _m(dust),
            "net_usdt": _m(net),
            "net_yuzde": float(pct),
            "aciklama": EXIT_LABELS_TR.get(reason, reason),
            "notlar": _json_list(notes),
            "usdttry": _text(self.engine.usdttry),
        })
        self._unprotected_notified.discard(position.id)
        self.audit.write(
            "demo_cikis",
            f"Demo pozisyon kapandı: {closed.sembol} #{closed.id} "
            f"{EXIT_LABELS_TR.get(reason, reason)} @ {closed.cikis_fiyati}, net {_usdt(net)} USDT "
            f"(%{pct:+.3f}); korumasız toplam {closed.korumasiz_toplam_ms / 1000:.1f} sn",
            kaynak=SOURCE_LOOP, ayrinti={"jeton": closed.jeton, "sebep": reason},
        )
        icon = {EXIT_TARGET: "🎯", EXIT_STOP: "🛑", EXIT_DUST: "🧹"}.get(reason, "↩️")
        kind = {EXIT_TARGET: KIND_TARGET, EXIT_STOP: KIND_STOP}.get(reason, KIND_EXIT)
        self._notify(
            f"{icon} {EXIT_LABELS_TR.get(reason, reason).upper()} (demo) — {closed.sembol}\n"
            f"Ortalama çıkış {closed.cikis_fiyati}, net {_usdt(net)} USDT (%{pct:+.3f})."
            + (f"\nSatılamayan küsurat {_m(dust)} hesapta kaldı." if reason == EXIT_DUST else ""),
            kind,
        )
        self._after_close(self.clock())

    def _after_close(self, now: datetime) -> None:
        limits = self.engine.limits
        snapshot = self.snapshot(now)
        notified = self.ledger.get_json(STATE_NOTIFIED_BREACHES, {})
        if not isinstance(notified, dict):
            notified = {}
        new: list[risk.Breach] = []
        for breach in risk.breaches(snapshot, limits, now):
            key = _breach_key(breach.tur, now, snapshot)
            if notified.get(breach.tur) == key:
                continue
            notified[breach.tur] = key
            new.append(breach)
        if new:
            self.ledger.set_json(STATE_NOTIFIED_BREACHES, notified)
            self.engine.halt(
                "Demo: " + "; ".join(item.etiket for item in new),
                "\n".join(item.aciklama for item in new),
                now=now, kind=KIND_LIMIT, source=SOURCE_RISK,
            )

    # --- dışarıya görünüm ---------------------------------------------------------------------

    def position_payload(self, position: DemoPosition, marks: dict[str, Decimal]) -> dict[str, Any]:
        view = self._view(position)
        econ = view.econ
        now_ms = self._now_ms()
        unprotected_now = (now_ms - position.korumasiz_baslangic_ms
                           if position.korumasiz_baslangic_ms is not None else 0)
        mark = marks.get(position.sembol)
        open_value: str | None = None
        if not position.bitti and econ.alinan_brut > ZERO and mark is not None:
            _, net, _ = net_result(econ, exit_price=mark)
            open_value = _usdt(net)
        return {
            "id": position.id,
            "jeton": position.jeton,
            "sembol": position.sembol,
            "periyot": position.periyot,
            "kaynak": position.kaynak,
            "kural": position.kural_etiketi or position.kural_kimligi,
            "durum": position.durum,
            "durum_tr": position.durum_tr,
            "aciklama": position.aciklama,
            "olusturma": istanbul_text(position.olusturma_utc),
            "giris": position.giris,
            "hedef": position.hedef,
            "stop": position.stop,
            "stop_limit": position.stop_limit,
            "miktar": position.miktar,
            "tutar_usdt": _usdt(position.dec("tutar_usdt")),
            "stop_zarari_usdt": _usdt(position.dec("stop_zarari_usdt")),
            "satis_miktari": position.bekleyen_miktar,
            "alinan": _m(econ.alinan_brut),
            "ortalama_giris": _text(econ.ortalama_giris),
            "satilan": _m(econ.satilan),
            "elde": _m(max(view.held, ZERO)),
            "gecerlilik": istanbul_text(from_ms(position.gecerlilik_bitis_ms)),
            "korumasiz_simdi_sn": round(unprotected_now / 1000, 1),
            "korumasiz_toplam_sn": round((position.korumasiz_toplam_ms + unprotected_now) / 1000,
                                         1),
            "korumasiz_azami_sn": round(max(position.korumasiz_azami_ms, unprotected_now) / 1000,
                                        1),
            "yeniden_fiyatlama": position.yeniden_fiyatlama,
            "anlik_net_usdt": open_value,
            "kapanis": istanbul_text(position.kapanis_utc) if position.kapanis_utc else None,
            "cikis_sebebi": EXIT_LABELS_TR.get(position.cikis_sebebi or "", position.cikis_sebebi),
            "cikis_fiyati": position.cikis_fiyati,
            "net_usdt": None if position.net_usdt is None else _usdt(position.dec("net_usdt")),
            "net_yuzde": None if position.net_yuzde is None else round(position.net_yuzde, 4),
            "notlar": position.not_listesi,
            "iptal_edilebilir": any(leg.rol == ROLE_ENTRY and leg.canli for leg in view.legs)
            and not position.bitti,
            "kapatilabilir": not position.bitti and econ.alinan_brut > ZERO,
            "emirler": [
                {
                    "kimlik": leg.istemci_kimligi,
                    "rol": leg.rol_tr,
                    "tur": leg.tur,
                    "taraf": leg.taraf,
                    "fiyat": leg.fiyat,
                    "stop_fiyati": leg.stop_fiyati,
                    "miktar": leg.miktar,
                    "dolan": leg.dolan,
                    "durum": leg.durum,
                    "sebep": leg.sebep,
                }
                for leg in view.legs
            ],
        }

    def summary(self) -> dict[str, Any]:
        period_id, _, _ = self.period()
        result: dict[str, Any] = {}
        closed = self.ledger.closed_in_period(period_id)
        for source in (risk.SOURCE_RULE, risk.SOURCE_MANUAL):
            items = [item for item in closed if item.kaynak == source]
            nets = [item.dec("net_usdt") for item in items]
            wins = sum(1 for value in nets if value > ZERO)
            reasons: dict[str, int] = {}
            for item in items:
                label = EXIT_LABELS_TR.get(item.cikis_sebebi or "", item.cikis_sebebi or "?")
                reasons[label] = reasons.get(label, 0) + 1
            result["kural" if source == risk.SOURCE_RULE else "elle"] = {
                "islem": len(items),
                "kazanan": wins,
                "isabet_orani": round(wins / len(items), 4) if items else None,
                "net_usdt": _usdt(sum(nets, ZERO)),
                "ortalama_net_yuzde": round(sum(item.net_yuzde or 0.0 for item in items)
                                            / len(items), 4) if items else None,
                "komisyon_usdt": _usdt(sum((item.dec("komisyon_usdt") for item in items), ZERO)),
                "korumasiz_toplam_sn": round(sum(item.korumasiz_toplam_ms for item in items)
                                             / 1000, 1),
                "korumasiz_azami_sn": round(max((item.korumasiz_azami_ms for item in items),
                                                default=0) / 1000, 1),
                "cikis_sebepleri": reasons,
            }
        return result

    def fills_csv(self) -> str:
        """Her dolum bir satır (gerçek dolumlar; Excel için ``;`` ve BOM)."""
        buffer = io.StringIO()
        buffer.write("﻿")
        columns = ["tarih_istanbul", "pozisyon", "cift", "yon", "emir_rolu", "fiyat", "miktar",
                   "tutar_usdt", "komisyon", "komisyon_varligi", "likidite", "islem_kimligi",
                   "kaynak"]
        writer = csv.DictWriter(buffer, fieldnames=columns, delimiter=";")
        writer.writeheader()
        for position in self.ledger.recent(10_000):
            roles = {leg.istemci_kimligi: leg.rol_tr for leg in self.ledger.legs(position.id)}
            for fill in self.ledger.fills(position.id):
                writer.writerow({
                    "tarih_istanbul": istanbul_text(from_ms(fill.zaman_ms)),
                    "pozisyon": position.id,
                    "cift": fill.sembol,
                    "yon": "ALIŞ" if fill.taraf == "BUY" else "SATIŞ",
                    "emir_rolu": roles.get(fill.emir_istemci, ""),
                    "fiyat": fill.fiyat,
                    "miktar": fill.miktar,
                    "tutar_usdt": fill.quote_miktar,
                    "komisyon": fill.komisyon,
                    "komisyon_varligi": fill.komisyon_varligi or "",
                    "likidite": "maker" if fill.maker else "taker",
                    "islem_kimligi": fill.islem_kimligi,
                    "kaynak": position.kaynak,
                })
        return buffer.getvalue()

    def status(self) -> dict[str, Any]:
        trader = self.trader
        stream = self.stream.status() if self.stream is not None else None
        book = self.book_stream.status() if self.book_stream is not None else None
        budget = trader.budget.status() if trader is not None else None
        counts = trader.order_counts() if trader is not None else None
        short, day = self._order_limits()
        reason = self.not_ready_reason()
        return {
            "kurulu": trader is not None,
            "hazir": reason is None,
            "hazir_degil": reason,
            "ortam": "Binance Demo Mode (sahte para)",
            "adres": None if trader is None else trader.base,
            "saat_farki_ms": None if trader is None else trader.offset_ms,
            "hesap_akisi": None if stream is None else {
                "bagli": stream.bagli, "abone": stream.abone,
                "yeniden_baglanma": stream.yeniden_baglanma, "olay": stream.olay_sayisi,
                "son_hata": stream.son_hata,
            },
            "defter_akisi": None if book is None else {
                "bagli": book.bagli, "yeniden_baglanma": book.yeniden_baglanma,
                "son_hata": book.son_hata,
            },
            "son_uzlastirma": istanbul_text(self.health.son_uzlastirma_utc)
            if self.health.son_uzlastirma_utc else None,
            "son_uzlastirma_ozet": self.health.son_uzlastirma_ozet,
            "uyarilar": list(self.health.uyarilar),
            "elle_emirler": dict(self.health.elle_emirler),
            "bakiyeler": {asset: {"serbest": _m(free), "kilitli": _m(locked)}
                          for asset, (free, locked) in sorted(self._balances.items())
                          if asset in {"USDT", "BNB"} | {
                              self._base_asset(s) for s in self.symbols}},
            "emir_sayaci": {
                "son_10sn": None if counts is None else counts.son_10sn,
                "son_1gun": None if counts is None else counts.son_1gun,
                "sinir_10sn": short, "sinir_gun": day,
            },
            "istek_butcesi": None if budget is None else {
                "yerel_1dk": budget.yerel_1dk, "yerel_tavan": budget.yerel_tavan,
                "borsa_1dk": budget.borsa_1dk, "reddedilen": budget.reddedilen,
                "engelli": budget.engelli, "son_hata": budget.son_hata,
            },
            "son_hata": self.health.son_hata,
            "olaylar": list(reversed(self.health.olaylar)),
        }


def _json_list(items: Sequence[str]) -> str:
    import json

    return json.dumps(list(items), ensure_ascii=False)


__all__ = [
    "DEMO_INFO_FILENAME",
    "DemoAccountView",
    "DemoExecutor",
    "PlaceResult",
]
