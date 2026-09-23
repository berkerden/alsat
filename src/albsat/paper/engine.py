"""Kâğıt işlem motoru: emirleri risk motorundan geçirir, mumlarla doldurur.

Akış:

1. Bir emir niyeti gelir (kural sinyali ya da kullanıcının elle girdiği).
2. Risk motoru bütün kapıları değerlendirir; biri kapalıysa emir açılmaz ve
   sebebi kaydedilir.
3. Açılan emir kâğıt defterine yazılır. Her yeni kapanmış 1 dakikalık mum
   ``process_candle``'a gelir; dolum kuralları (``paper.fills``) girişi,
   stopu, hedefi ve süre dolumunu işletir.
4. Kapanan her işlemden sonra risk motorunun işlem sonrası sınırları
   sınanır. Bir sınır aşıldıysa bütün coinler "Sadece Öneri"ye çekilir,
   bekleyen girişler iptal edilir, bildirim gider.

**Çevrimiçi / çevrimdışı ayrımı.** Uygulama kapalıyken (Mac uyurken, internet
yokken) kaçırılan mumlar sonradan işlenir. O mumlarda borsa tarafında olacak
şeyler olur (limit giriş dolabilir, stop ve hedef çalışabilir), ama
**uygulama tarafında** olacak şeyler olmaz: süresi dolan girişi iptal etmek
ya da süresi dolan pozisyonu kapatmak uygulamanın işidir ve uygulama o an
çalışmıyordu. Gerçek hesapta da böyle olur; Binance'te bir limit emri kendi
kendine süresi dolup silinmez. Bu yüzden ``online=False`` işlenen mumlarda
yalnızca borsa tarafı çalışır, gecikmiş uygulama işleri ``apply_deferred``
ile açılışta yapılır.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from albsat.core.audit import SOURCE_LOOP, SOURCE_RISK, AuditLog
from albsat.core.clock import (
    as_utc,
    day_start,
    from_ms,
    iso,
    istanbul_text,
    month_start,
    parse_utc,
    to_ms,
    utc_now,
    week_start,
)
from albsat.core.costs import FeePayment, LegCost, RoundTrip
from albsat.core.fees import Liquidity, Side
from albsat.core.filters import SymbolRules
from albsat.core.money import ZERO, format_for_api
from albsat.data.klines import interval_ms
from albsat.modes.state import MODE_ADVICE, MODE_LABELS_TR, MODE_PAPER, ModeStore
from albsat.notify.base import (
    KIND_CANCEL,
    KIND_EXIT,
    KIND_FILL,
    KIND_KILL,
    KIND_LIMIT,
    KIND_MODE,
    KIND_ORDER,
    KIND_STOP,
    KIND_TARGET,
    Notifier,
)
from albsat.paper import fills
from albsat.paper.fills import Candle, PaperCosts
from albsat.paper.ledger import (
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_PENDING,
    PaperLedger,
    PaperOrder,
    new_client_id,
)
from albsat.risk import engine as risk
from albsat.risk.limits import LimitStore, RiskLimits
from albsat.risk.market import Gate, MarketState

STATE_LAST_CANDLE = "son_1m_ms:"
STATE_STREAK_RESET = "art_arda_sifirlama_utc"
STATE_DISABLED_RULES = "durdurulan_kurallar"
STATE_NOTIFIED_BREACHES = "bildirilen_sinirlar"


@dataclass(frozen=True)
class OrderMeta:
    """Emrin risk kararına girmeyen ama kayda geçen bilgileri."""

    kural_etiketi: str | None = None
    sinyal_mumu_utc: str | None = None
    #: Giriş emrinin kaç mum (emrin periyodunda) geçerli kalacağı.
    gecerlilik_mum: int = 1
    #: Pozisyonun en fazla kaç mum tutulacağı; sonra piyasa emriyle kapanır.
    azami_tutma_mum: int = 4
    beklenen_hedef_net_yuzde: float | None = None
    beklenen_ortalama_yuzde: float | None = None
    yeniden_fiyatlama: int = 0
    notlar: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountView:
    """Kâğıt hesabının arayüzde gösterilecek hâli."""

    donem_id: int
    baslangic_usdt: Decimal
    baslangic_utc: str
    nakit_usdt: Decimal
    kilitli_usdt: Decimal
    serbest_usdt: Decimal
    coinler: dict[str, Decimal]
    ozsermaye_usdt: Decimal | None
    getiri_yuzde: Decimal | None
    #: Değeri bilinmeyen coin varsa (fiyat yok) özsermaye hesaplanmaz.
    eksik_fiyat: tuple[str, ...] = ()


@dataclass
class EngineEvents:
    """Bir işlem turunda olanlar (testler ve canlı döngü için)."""

    dolan: list[PaperOrder] = field(default_factory=list)
    kapanan: list[PaperOrder] = field(default_factory=list)
    iptal: list[PaperOrder] = field(default_factory=list)
    sinir_asimlari: list[risk.Breach] = field(default_factory=list)
    durdurulan_kurallar: list[str] = field(default_factory=list)

    def extend(self, other: EngineEvents) -> None:
        self.dolan.extend(other.dolan)
        self.kapanan.extend(other.kapanan)
        self.iptal.extend(other.iptal)
        self.sinir_asimlari.extend(other.sinir_asimlari)
        self.durdurulan_kurallar.extend(other.durdurulan_kurallar)


def _m(value: Decimal) -> str:
    return format_for_api(value)


def _usdt(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


class PaperEngine:
    """Kâğıt hesabının tek sahibi. Bütün durum değişiklikleri buradan geçer."""

    def __init__(
        self,
        root: Path | str,
        *,
        symbols: Sequence[str],
        notifier: Notifier,
        costs_for: Callable[[str], PaperCosts],
        rules_for: Callable[[str], SymbolRules | None],
        modes: ModeStore | None = None,
    ) -> None:
        self.root = Path(root)
        self.symbols = tuple(symbols)
        self.ledger = PaperLedger.in_directory(self.root)
        self.limit_store = LimitStore.in_directory(self.root)
        self.audit = AuditLog.in_directory(self.root)
        self.modes = modes or ModeStore.in_directory(self.root, self.symbols)
        self.notifier = notifier
        self.costs_for = costs_for
        self.rules_for = rules_for
        #: Son bilinen USDTTRY kuru (CSV'nin TRY sütunu için); yoksa boş kalır.
        self.usdttry: Decimal | None = None
        #: Aynı anda iki iş parçacığı (arayüz, canlı döngü, Telegram) hesabı
        #: değiştirmesin.
        self.lock = threading.RLock()

    # --- okuma ----------------------------------------------------------

    @property
    def limits(self) -> RiskLimits:
        return self.limit_store.read()

    def period(self) -> tuple[int, Decimal, str]:
        return self.ledger.current_period(default_start_usdt=self.limits.butce_usdt)

    def disabled_rules(self) -> list[str]:
        value = self.ledger.get_json(STATE_DISABLED_RULES, [])
        return [str(item) for item in value] if isinstance(value, list) else []

    def account(self, marks: dict[str, Decimal] | None = None) -> AccountView:
        marks = marks or {}
        period_id, start, started = self.period()
        orders = self.ledger.in_period(period_id)
        cash = start
        locked = ZERO
        coins: dict[str, Decimal] = {}
        for order in orders:
            if order.dolum_utc:
                cash -= order.dec("tutar_usdt")
                coins[order.sembol] = coins.get(order.sembol, ZERO) + order.dec("alinan")
            if order.durum == STATUS_CLOSED:
                cash += order.dec("gelir_usdt")
                coins[order.sembol] = coins.get(order.sembol, ZERO) - order.dec("satilacak")
            if order.durum == STATUS_PENDING:
                locked += order.dec("tutar_usdt")
        coins = {key: value for key, value in coins.items() if value != ZERO}
        missing = tuple(sorted(key for key in coins if key not in marks))
        equity: Decimal | None = None
        change: Decimal | None = None
        if not missing:
            equity = cash + sum((value * marks[key] for key, value in coins.items()), ZERO)
            change = (equity - start) / start * Decimal(100) if start > ZERO else None
        return AccountView(
            donem_id=period_id,
            baslangic_usdt=start,
            baslangic_utc=started,
            nakit_usdt=cash,
            kilitli_usdt=locked,
            serbest_usdt=cash - locked,
            coinler=coins,
            ozsermaye_usdt=equity,
            getiri_yuzde=change,
            eksik_fiyat=missing,
        )

    def dust(self, sembol: str) -> Decimal:
        """Hesapta bu coinden, açık bir pozisyona ait olmayan küsurat.

        Doldurulmuş her işlemin ``alinan``'ı hesaba girer, ``satilacak``'ı
        (açık pozisyonda satılmak üzere ayrılmış, kapananda satılmış) çıkar;
        kalan, önceki işlemlerden artan tozdur.
        """
        period_id, _, _ = self.period()
        total = ZERO
        for order in self.ledger.in_period(period_id):
            if order.sembol == sembol and order.dolum_utc:
                total += order.dec("alinan") - order.dec("satilacak")
        return max(total, ZERO)

    def snapshot(self, now: datetime) -> risk.AccountSnapshot:
        period_id, start, _ = self.period()
        closed = self.ledger.closed_in_period(period_id)
        active = [item for item in self.ledger.in_period(period_id)
                  if item.durum in (STATUS_PENDING, STATUS_OPEN)]
        today = day_start(now)
        entries_today = sum(
            1
            for item in self.ledger.in_period(period_id)
            if (parse_utc(item.olusturma_utc) or today) >= today
        )
        reset = parse_utc(self.ledger.get_state(STATE_STREAK_RESET))
        return risk.AccountSnapshot(
            baslangic_usdt=start,
            serbest_usdt=self.account().serbest_usdt,
            kapanan=tuple(
                risk.ClosedTrade(
                    sembol=item.sembol,
                    periyot=item.periyot,
                    kapanis_utc=item.kapanis or now,
                    net_usdt=item.dec("net_usdt"),
                    kaynak=item.kaynak,
                    kural_kimligi=item.kural_kimligi,
                    net_yuzde=float(item.net_yuzde or 0.0),
                )
                for item in closed
            ),
            acik=tuple(
                risk.OpenExposure(
                    sembol=item.sembol,
                    tutar_usdt=item.dec("tutar_usdt"),
                    stop_zarari_usdt=item.dec("stop_zarari_usdt"),
                    bekleyen=item.durum == STATUS_PENDING,
                )
                for item in active
            ),
            girisler_bugun=entries_today,
            art_arda_sifirlama_utc=reset,
        )

    def rule_check(self, rule_id: str, now: datetime) -> risk.PerformanceCheck | None:
        """Kuralın kâğıt sonuçları beklentisiyle uyumlu mu? (SPEC §4.6)

        Yalnızca kural işlemleri sayılır; kural elle yeniden açıldıysa o andan
        sonrakiler. Beklenti kayıtlı değilse ``None``.
        """
        since = self.ledger.get_json("kural_sinama_baslangic", {})
        start = parse_utc(since.get(rule_id)) if isinstance(since, dict) else None
        trades = [
            item
            for item in self.ledger.all_closed()
            if item.kural_kimligi == rule_id
            and item.kaynak == risk.SOURCE_RULE
            and (start is None or (item.kapanis or now) > start)
        ]
        expected = next(
            (item.beklenen_ortalama_yuzde for item in reversed(trades)
             if item.beklenen_ortalama_yuzde is not None),
            None,
        )
        if expected is None:
            return None
        return risk.performance_check(
            rule_id,
            [float(item.net_yuzde or 0.0) for item in trades],
            expected_pct=float(expected),
            limits=self.limits,
        )

    def rule_checks(self, now: datetime) -> list[risk.PerformanceCheck]:
        rule_ids = sorted({
            item.kural_kimligi for item in self.ledger.all_closed()
            if item.kaynak == risk.SOURCE_RULE and item.kural_kimligi
        })
        return [check for rule_id in rule_ids
                if (check := self.rule_check(rule_id, now)) is not None]

    # --- maliyet turları ----------------------------------------------------

    def round_trips(self, sembol: str) -> tuple[RoundTrip, RoundTrip]:
        """Kâğıt işlemin gerçek emir tipleriyle tur maliyetleri.

        Giriş ``LIMIT_MAKER`` (maker), hedef ``LIMIT_MAKER`` (maker), stop
        ``STOP_LOSS`` (taker). Araştırma girişi taker saymıştı (temkinli);
        kâğıt işlem gerçekte gönderilecek emir tiplerini kullanır.
        """
        costs = self.costs_for(sembol)

        def leg(side: Side, liquidity: Liquidity) -> LegCost:
            return LegCost(side=side, liquidity=liquidity, rate=costs.rate(side, liquidity))

        entry = leg(Side.BUY, Liquidity.MAKER)
        to_target = RoundTrip(entry, leg(Side.SELL, Liquidity.MAKER), FeePayment.FROM_RECEIVED)
        to_stop = RoundTrip(entry, leg(Side.SELL, Liquidity.TAKER), FeePayment.FROM_RECEIVED)
        return to_target, to_stop

    # --- emir açma ----------------------------------------------------------

    def evaluate(
        self,
        intent: risk.OrderIntent,
        *,
        market: MarketState,
        now: datetime,
        best_ask: Decimal | None = None,
    ) -> risk.RiskDecision:
        """Risk kapıları; ``best_ask`` verilirse limit-maker kuralı da eklenir."""
        decision = self._evaluate_risk(intent, market=market, now=now)
        if best_ask is None:
            return decision
        gate = post_only_gate(intent.giris, best_ask)
        return risk.RiskDecision(
            izin=decision.izin and gate.gecti,
            kapilar=(*decision.kapilar, gate),
            pozisyon=decision.pozisyon,
            hedef_net_yuzde=decision.hedef_net_yuzde,
        )

    def _evaluate_risk(
        self, intent: risk.OrderIntent, *, market: MarketState, now: datetime
    ) -> risk.RiskDecision:
        to_target, to_stop = self.round_trips(intent.sembol)
        mode = self.modes.get(intent.sembol)
        return risk.evaluate(
            intent,
            snapshot=self.snapshot(now),
            limits=self.limits,
            market=market,
            now=now,
            round_trip_to_target=to_target,
            round_trip_to_stop=to_stop,
            exit_slippage_pct=self.costs_for(intent.sembol).kayma_yuzde,
            mode_open=mode == MODE_PAPER,
            mode_text=(
                f"{intent.sembol} kâğıt işlem modunda."
                if mode == MODE_PAPER
                else f"{intent.sembol} şu an '{MODE_LABELS_TR.get(mode, mode)}' modunda; "
                "kâğıt emir için Kâğıt İşlem moduna alın."
            ),
            disabled_rules=self.disabled_rules(),
        )

    def place(
        self,
        intent: risk.OrderIntent,
        *,
        market: MarketState,
        now: datetime,
        meta: OrderMeta | None = None,
        source: str = SOURCE_LOOP,
        best_ask: Decimal | None = None,
    ) -> tuple[risk.RiskDecision, PaperOrder | None]:
        """Risk kapılarından geçerse kâğıt emri açar."""
        meta = meta or OrderMeta()
        with self.lock:
            decision = self.evaluate(intent, market=market, now=now, best_ask=best_ask)
            label = meta.kural_etiketi or ("elle emir" if intent.kaynak == risk.SOURCE_MANUAL
                                           else intent.kural_kimligi or "")
            if not decision.izin or decision.pozisyon is None:
                self.audit.write(
                    "emir_reddedildi",
                    f"{intent.sembol} {label}: {decision.ozet_tr}",
                    kaynak=SOURCE_RISK,
                    ayrinti={
                        "sembol": intent.sembol,
                        "kaynak_tur": intent.kaynak,
                        "kapali": ", ".join(item.ad for item in decision.kapali_kapilar),
                    },
                    now=now,
                )
                return decision, None

            size = decision.pozisyon
            period_id, _, _ = self.period()
            step = interval_ms(intent.periyot)
            now_ms = to_ms(now)
            # Kural 2 (fills): emir verildiği dakikanın mumunda dolmaz.
            active_from = (now_ms // 60_000 + 1) * 60_000
            order = self.ledger.insert(
                {
                    "donem_id": period_id,
                    "istemci_kimligi": new_client_id(intent.kaynak),
                    "olusturma_utc": iso(now),
                    "aktif_ms": active_from,
                    "sembol": intent.sembol,
                    "periyot": intent.periyot,
                    "kaynak": intent.kaynak,
                    "kural_kimligi": intent.kural_kimligi,
                    "kural_etiketi": meta.kural_etiketi,
                    "sinyal_mumu_utc": meta.sinyal_mumu_utc,
                    "giris": _m(intent.giris),
                    "hedef": _m(intent.hedef),
                    "stop": _m(intent.stop),
                    "miktar": _m(size.miktar),
                    "tutar_usdt": _m(size.tutar_usdt),
                    "stop_zarari_usdt": _m(size.stop_zarari_usdt),
                    "gecerlilik_bitis_ms": active_from + step * max(meta.gecerlilik_mum, 1),
                    "azami_tutma_ms": step * max(meta.azami_tutma_mum, 1),
                    "beklenen_hedef_net_yuzde": (
                        meta.beklenen_hedef_net_yuzde
                        if meta.beklenen_hedef_net_yuzde is not None
                        else float(decision.hedef_net_yuzde or 0)
                    ),
                    "beklenen_ortalama_yuzde": meta.beklenen_ortalama_yuzde,
                    "yeniden_fiyatlama": meta.yeniden_fiyatlama,
                    "durum": STATUS_PENDING,
                    "notlar": _json_list(meta.notlar),
                }
            )
            self.audit.write(
                "emir",
                f"Kâğıt giriş emri: {order.sembol} {order.miktar} adet @ {order.giris} "
                f"(hedef {order.hedef}, stop {order.stop})",
                kaynak=source,
                ayrinti={
                    "istemci_kimligi": order.istemci_kimligi,
                    "kaynak_tur": order.kaynak,
                    "kural": order.kural_kimligi,
                    "tutar_usdt": order.tutar_usdt,
                    "stop_zarari_usdt": order.stop_zarari_usdt,
                    "baglayici": size.baglayici.value,
                },
                now=now,
            )
            self.notifier.send(
                f"📝 KÂĞIT EMİR — {order.sembol} {order.periyot}\n"
                f"Kaynak: {label}\n"
                f"Giriş (limit) {order.giris}, hedef {order.hedef}, stop {order.stop}\n"
                f"Miktar {order.miktar} ≈ {_usdt(order.dec('tutar_usdt'))} USDT; stopta "
                f"zarar ≈ {_usdt(order.dec('stop_zarari_usdt'))} USDT\n"
                f"{size.aciklama_tr}\n"
                f"Geçerlilik: {istanbul_text(_ms_to_dt(order.gecerlilik_bitis_ms))}'a kadar.",
                kind=KIND_ORDER,
            )
            return decision, order

    # --- mum işleme ---------------------------------------------------------

    def last_candle_ms(self, sembol: str) -> int | None:
        value = self.ledger.get_state(STATE_LAST_CANDLE + sembol)
        return int(value) if value else None

    def process_candle(
        self, sembol: str, candle: Candle, *, online: bool = True, now: datetime | None = None
    ) -> EngineEvents:
        """Kapanmış bir 1 dakikalık mumu bu sembolün emir ve pozisyonlarına uygular."""
        events = EngineEvents()
        with self.lock:
            last = self.last_candle_ms(sembol)
            if last is not None and candle.open_time_ms <= last:
                return events  # aynı mum iki kez işlenmez
            rules = self.rules_for(sembol)
            costs = self.costs_for(sembol)
            for order in self.ledger.active():
                if order.sembol != sembol:
                    continue
                if order.durum == STATUS_PENDING:
                    self._step_pending(order, candle, rules, costs, online, events)
                elif order.durum == STATUS_OPEN:
                    self._step_open(order, candle, rules, costs, online, events)
            self.ledger.set_state(STATE_LAST_CANDLE + sembol, str(candle.open_time_ms))
            if events.kapanan:
                self._after_close(now or _ms_to_dt(candle.close_time_ms), events)
        return events

    def _step_pending(
        self,
        order: PaperOrder,
        candle: Candle,
        rules: SymbolRules | None,
        costs: PaperCosts,
        online: bool,
        events: EngineEvents,
    ) -> None:
        price = order.dec("giris")
        if fills.entry_fills(candle, price=price, active_from_ms=order.aktif_ms):
            entry = fills.fill_entry(
                price=price,
                quantity=order.dec("miktar"),
                costs=costs,
                rules=rules,
                carried_dust=self.dust(order.sembol),
            )
            filled = self.ledger.update(
                order.id,
                {
                    "durum": STATUS_OPEN,
                    "dolum_utc": iso(_ms_to_dt(candle.close_time_ms)),
                    "dolum_ms": candle.close_time_ms,
                    "tutar_usdt": _m(entry.maliyet_usdt),
                    "komisyon_coin": _m(entry.komisyon_coin),
                    "alinan": _m(entry.alinan),
                    "onceki_toz": _m(entry.onceki_toz),
                    "satilacak": _m(entry.satilacak),
                    "toz": _m(entry.toz),
                    "usdttry": _m(self.usdttry) if self.usdttry else None,
                },
            )
            events.dolan.append(filled)
            self.audit.write(
                "dolum",
                f"Kâğıt giriş doldu: {filled.sembol} {filled.miktar} @ {filled.giris}",
                kaynak=SOURCE_LOOP,
                ayrinti={"istemci_kimligi": filled.istemci_kimligi, "online": online},
            )
            self.notifier.send(
                f"✅ DOLDU (kâğıt) — {filled.sembol}: {filled.miktar} adet @ {filled.giris}\n"
                f"Hedef {filled.hedef}, stop {filled.stop}. Komisyon "
                f"{filled.komisyon_coin} {filled.sembol.removesuffix('USDT')}.",
                kind=KIND_FILL,
            )
            # Kural 6: dolum mumunda stop görüldüyse aynı mumda kapanır.
            trigger = fills.exit_trigger(
                candle,
                stop=filled.dec("stop"),
                target=filled.dec("hedef"),
                slippage_pct=costs.kayma_yuzde,
                rules=rules,
                allow_target=False,
            )
            if trigger is not None:
                self._close(filled, trigger[0], trigger[1], candle.close_time_ms, costs, events)
            return
        if online and candle.close_time_ms >= order.gecerlilik_bitis_ms:
            self._cancel(order, "Giriş fiyatı geçerlilik süresi içinde gelmedi.", events)

    def _step_open(
        self,
        order: PaperOrder,
        candle: Candle,
        rules: SymbolRules | None,
        costs: PaperCosts,
        online: bool,
        events: EngineEvents,
    ) -> None:
        if order.dolum_ms is not None and candle.open_time_ms < order.dolum_ms:
            return  # dolum mumu zaten işlendi
        trigger = fills.exit_trigger(
            candle,
            stop=order.dec("stop"),
            target=order.dec("hedef"),
            slippage_pct=costs.kayma_yuzde,
            rules=rules,
        )
        if trigger is not None:
            self._close(order, trigger[0], trigger[1], candle.close_time_ms, costs, events)
            return
        deadline = (order.dolum_ms or 0) + order.azami_tutma_ms
        if online and candle.close_time_ms >= deadline:
            price = fills.market_exit_price(
                candle.close, slippage_pct=costs.kayma_yuzde, rules=rules
            )
            self._close(order, fills.EXIT_TIME, price, candle.close_time_ms, costs, events)

    def apply_deferred(
        self, sembol: str, last: Candle, *, now: datetime
    ) -> EngineEvents:
        """Uygulama kapalıyken yapılamayan iptal ve süre kapanışlarını şimdi yapar."""
        events = EngineEvents()
        with self.lock:
            rules = self.rules_for(sembol)
            costs = self.costs_for(sembol)
            for order in self.ledger.active():
                if order.sembol != sembol:
                    continue
                expired = last.close_time_ms >= order.gecerlilik_bitis_ms
                if order.durum == STATUS_PENDING and expired:
                    self._cancel(
                        order,
                        "Geçerlilik süresi uygulama kapalıyken doldu; açılışta iptal edildi.",
                        events,
                    )
                elif order.durum == STATUS_OPEN and last.close_time_ms >= (
                    (order.dolum_ms or 0) + order.azami_tutma_ms
                ):
                    price = fills.market_exit_price(
                        last.close, slippage_pct=costs.kayma_yuzde, rules=rules
                    )
                    self._close(order, fills.EXIT_TIME, price, last.close_time_ms, costs, events,
                                note="Tutma süresi uygulama kapalıyken doldu; açılışta "
                                     "piyasa fiyatından kapatıldı.")
            if events.kapanan:
                self._after_close(now, events)
        return events

    def _cancel(self, order: PaperOrder, reason: str, events: EngineEvents,
                source: str = SOURCE_LOOP) -> PaperOrder:
        cancelled = self.ledger.update(
            order.id, {"durum": STATUS_CANCELLED, "iptal_sebebi": reason,
                       "cikis_utc": iso(utc_now())}
        )
        events.iptal.append(cancelled)
        self.audit.write(
            "iptal",
            f"Kâğıt giriş iptal: {order.sembol} — {reason}",
            kaynak=source,
            ayrinti={"istemci_kimligi": order.istemci_kimligi},
        )
        self.notifier.send(
            f"⏹ İPTAL (kâğıt) — {order.sembol} giriş emri @ {order.giris}\n{reason}",
            kind=KIND_CANCEL,
        )
        return cancelled

    def _close(
        self,
        order: PaperOrder,
        reason: str,
        price: Decimal,
        when_ms: int,
        costs: PaperCosts,
        events: EngineEvents,
        *,
        note: str | None = None,
        source: str = SOURCE_LOOP,
    ) -> PaperOrder:
        quantity = order.dec("satilacak")
        exit_ = fills.fill_exit(reason=reason, price=price, quantity=quantity, costs=costs)
        net, pct = fills.net_result(
            cost_usdt=order.dec("tutar_usdt"),
            exit_=exit_,
            dust_change=order.dec("toz") - order.dec("onceki_toz"),
        )
        notes = order.not_listesi + ([note] if note else [])
        closed = self.ledger.update(
            order.id,
            {
                "durum": STATUS_CLOSED,
                "cikis_utc": iso(_ms_to_dt(when_ms)),
                "cikis_fiyati": _m(price),
                "cikis_sebebi": reason,
                "gelir_usdt": _m(exit_.gelir_usdt),
                "cikis_komisyon_usdt": _m(exit_.komisyon_usdt),
                "net_usdt": _m(net),
                "net_yuzde": float(pct),
                "notlar": _json_list(notes),
                "usdttry": _m(self.usdttry) if self.usdttry else order.usdttry,
            },
        )
        events.kapanan.append(closed)
        self.audit.write(
            "cikis",
            f"Kâğıt pozisyon kapandı: {closed.sembol} {fills.EXIT_LABELS_TR[reason]} @ "
            f"{closed.cikis_fiyati}, net {_usdt(net)} USDT (%{pct:+.3f})",
            kaynak=source,
            ayrinti={"istemci_kimligi": closed.istemci_kimligi, "sebep": reason},
        )
        icon = {"hedef": "🎯", "stop": "🛑"}.get(reason, "↩️")
        kind = {"hedef": KIND_TARGET, "stop": KIND_STOP}.get(reason, KIND_EXIT)
        expected = order.beklenen_hedef_net_yuzde
        expected_text = (
            f"\nKartta hedefe gidişin beklenen neti %{expected:+.3f}." if expected is not None
            and reason == fills.EXIT_TARGET else ""
        )
        self.notifier.send(
            f"{icon} {fills.EXIT_LABELS_TR[reason].upper()} (kâğıt) — {closed.sembol}\n"
            f"Çıkış {closed.cikis_fiyati}, net {_usdt(net)} USDT (%{pct:+.3f})"
            f"{expected_text}",
            kind=kind,
        )
        return closed

    # --- işlem sonrası denetim -----------------------------------------------

    def _after_close(self, now: datetime, events: EngineEvents) -> None:
        limits = self.limits
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
            events.sinir_asimlari.extend(new)
            self._halt(
                "; ".join(item.etiket for item in new),
                "\n".join(item.aciklama for item in new),
                now=now,
                kind=KIND_LIMIT,
                events=events,
            )

        # Performans bozulma koruması: yalnızca kural işlemleri, kural başına.
        disabled = set(self.disabled_rules())
        for rule_id in {item.kural_kimligi for item in events.kapanan if item.kural_kimligi}:
            if rule_id in disabled:
                continue
            check = self.rule_check(rule_id, now)
            if check is not None and check.bozuk:
                disabled.add(rule_id)
                self.ledger.set_json(STATE_DISABLED_RULES, sorted(disabled))
                events.durdurulan_kurallar.append(rule_id)
                self.audit.write(
                    "kural_durduruldu", check.aciklama, kaynak=SOURCE_RISK,
                    ayrinti={"kural": rule_id}, now=now,
                )
                self.notifier.send(
                    f"⚠️ KURAL DURDURULDU — {rule_id}\n{check.aciklama}\n"
                    "Kâğıt sonuçları beklentinin anlamlı ölçüde altında. Arayüzden elle "
                    "yeniden açılana kadar bu kural emir açmaz.",
                    kind=KIND_LIMIT,
                )

    def _halt(self, title: str, detail: str, *, now: datetime, kind: str,
              events: EngineEvents, source: str = SOURCE_RISK) -> list[str]:
        """Otomatik işlemi kapatır: kâğıt modundaki coinler Sadece Öneri'ye iner,
        bekleyen girişler iptal edilir. Açık pozisyonların stop ve hedefi yerinde kalır."""
        switched: list[str] = []
        for symbol in self.modes.paper_symbols():
            self.modes.set(symbol, MODE_ADVICE)
            switched.append(symbol)
        for order in self.ledger.active():
            if order.durum == STATUS_PENDING:
                self._cancel(order, f"Otomatik işlem durduruldu: {title}.", events, source=source)
        self.audit.write(
            "durdurma",
            f"Otomatik işlem durduruldu: {title}",
            kaynak=source,
            ayrinti={"coinler": ",".join(switched), "aciklama": detail},
            now=now,
        )
        self.notifier.send(
            f"⛔ OTOMATİK İŞLEM DURDU — {title}\n{detail}\n"
            f"Kâğıt işlemden çıkarılan coinler: {', '.join(switched) or 'yok'}. Açık "
            "pozisyonların stop ve hedefi yerinde. Yeniden başlatmak arayüzden elle yapılır.",
            kind=kind,
        )
        return switched

    # --- kullanıcı eylemleri --------------------------------------------------

    def set_mode(self, sembol: str, mode: str, *, source: str, now: datetime) -> tuple[str, str]:
        with self.lock:
            old, new = self.modes.set(sembol, mode)
            if old != new:
                self.audit.write(
                    "mod",
                    f"{sembol}: {MODE_LABELS_TR.get(old, old)} → {MODE_LABELS_TR.get(new, new)}",
                    kaynak=source,
                    ayrinti={"sembol": sembol, "eski": old, "yeni": new},
                    now=now,
                )
                self.notifier.send(
                    f"🔁 MOD — {sembol}: {MODE_LABELS_TR.get(old, old)} → "
                    f"{MODE_LABELS_TR.get(new, new)}",
                    kind=KIND_MODE,
                )
            return old, new

    def kill_switch(
        self,
        *,
        close_positions: bool,
        marks: dict[str, Decimal],
        source: str,
        now: datetime,
    ) -> EngineEvents:
        """ACİL DURDUR (SPEC §4.6): kâğıt işlemi kapat, bekleyenleri iptal et,
        istenirse açık pozisyonları piyasa fiyatından kapat."""
        events = EngineEvents()
        with self.lock:
            title = "ACİL DURDUR"
            switched = self._halt(
                title,
                "Kullanıcı acil durdurmaya bastı."
                + (" Açık pozisyonlar piyasa fiyatından kapatılıyor." if close_positions
                   else " Açık pozisyonlar stop ve hedefiyle yerinde bırakıldı."),
                now=now,
                kind=KIND_KILL,
                events=events,
                source=source,
            )
            if close_positions:
                for order in self.ledger.active():
                    if order.durum != STATUS_OPEN:
                        continue
                    mark = marks.get(order.sembol)
                    if mark is None:
                        self.notifier.send(
                            f"⚠️ {order.sembol} pozisyonu kapatılamadı: güncel fiyat yok. "
                            "Stop ve hedef yerinde duruyor.",
                            kind=KIND_KILL,
                        )
                        continue
                    costs = self.costs_for(order.sembol)
                    price = fills.market_exit_price(
                        mark, slippage_pct=costs.kayma_yuzde, rules=self.rules_for(order.sembol)
                    )
                    self._close(order, fills.EXIT_KILL, price, to_ms(now), costs, events,
                                source=source)
            self.audit.write(
                "acil_durdur",
                "Acil durdurma çalıştı",
                kaynak=source,
                ayrinti={"coinler": ",".join(switched), "pozisyonlar_kapatildi": close_positions},
                now=now,
            )
            if events.kapanan:
                self._after_close(now, events)
        return events

    def cancel(self, order_id: int, *, source: str) -> PaperOrder:
        with self.lock:
            order = self.ledger.get(order_id)
            if order is None or order.durum != STATUS_PENDING:
                raise ValueError("Yalnızca bekleyen giriş emri iptal edilebilir.")
            return self._cancel(order, "Kullanıcı iptal etti.", EngineEvents(), source=source)

    def close(self, order_id: int, *, mark: Decimal | None, source: str,
              now: datetime) -> PaperOrder:
        with self.lock:
            order = self.ledger.get(order_id)
            if order is None or order.durum != STATUS_OPEN:
                raise ValueError("Yalnızca açık pozisyon kapatılabilir.")
            if mark is None:
                raise ValueError(f"{order.sembol} için güncel fiyat yok; kapatılamadı.")
            costs = self.costs_for(order.sembol)
            price = fills.market_exit_price(
                mark, slippage_pct=costs.kayma_yuzde, rules=self.rules_for(order.sembol)
            )
            events = EngineEvents()
            closed = self._close(order, fills.EXIT_MANUAL, price, to_ms(now), costs, events,
                                 source=source)
            self._after_close(now, events)
            return closed

    def reset_streak(self, *, source: str, now: datetime) -> None:
        with self.lock:
            self.ledger.set_state(STATE_STREAK_RESET, iso(now))
            self.audit.write("art_arda_sifirlama", "Art arda kayıp sayacı elle sıfırlandı",
                             kaynak=source, now=now)

    def enable_rule(self, rule_id: str, *, source: str, now: datetime) -> None:
        with self.lock:
            disabled = [item for item in self.disabled_rules() if item != rule_id]
            self.ledger.set_json(STATE_DISABLED_RULES, disabled)
            since = self.ledger.get_json("kural_sinama_baslangic", {})
            since = since if isinstance(since, dict) else {}
            since[rule_id] = iso(now)
            self.ledger.set_json("kural_sinama_baslangic", since)
            self.audit.write("kural_acildi", f"{rule_id} elle yeniden açıldı; sınama sıfırlandı",
                             kaynak=source, now=now)

    def reset_account(self, *, source: str, now: datetime) -> int:
        with self.lock:
            if self.ledger.active():
                raise ValueError(
                    "Bekleyen emir ya da açık pozisyon varken kâğıt hesap sıfırlanamaz."
                )
            period_id = self.ledger.reset_period(start_usdt=self.limits.butce_usdt, now=now)
            self.ledger.set_json(STATE_NOTIFIED_BREACHES, {})
            self.audit.write(
                "hesap_sifirlama",
                f"Kâğıt hesap sıfırlandı; yeni dönem {self.limits.butce_usdt} USDT ile başladı",
                kaynak=source,
                now=now,
            )
            return period_id


def post_only_gate(entry: Decimal, best_ask: Decimal) -> Gate:
    """Giriş ``LIMIT_MAKER`` emridir: fiyat en iyi satışa eşit ya da üstündeyse
    emir hemen eşleşeceği için borsa onu reddeder (``-2010``). Kâğıt işlem de
    reddeder; "bu fiyattan hemen alınırdı" varsayımı piyasa emri demektir ve
    taker komisyonu ile kayma gerektirir."""
    passed = entry < best_ask
    return Gate(
        "limit_maker",
        "Limit-maker kuralı",
        passed,
        (
            f"Giriş {entry}, en iyi satış {best_ask}: emir deftere yazılır."
            if passed
            else f"Giriş {entry}, en iyi satış {best_ask} ya da altında; limit-maker emir "
            "hemen eşleşeceği için borsa reddederdi. Girişi en iyi satışın altına "
            "yazın."
        ),
    )


def _breach_key(kind: str, now: datetime, snapshot: risk.AccountSnapshot) -> str:
    """Aynı sınır aynı dönemde iki kez bildirilmesin diye dönem anahtarı."""
    if kind == risk.BREACH_DAILY:
        return iso(day_start(now))
    if kind == risk.BREACH_WEEKLY:
        return iso(week_start(now))
    if kind == risk.BREACH_MONTHLY:
        return iso(month_start(now))
    last = snapshot.kapanan[-1].kapanis_utc if snapshot.kapanan else now
    return iso(as_utc(last))


def _ms_to_dt(value: int) -> datetime:
    return from_ms(value)


def _json_list(items: Sequence[str]) -> str:
    return json.dumps(list(items), ensure_ascii=False)


def time_left_text(deadline_ms: int, now: datetime) -> str:
    left = timedelta(milliseconds=deadline_ms) - timedelta(milliseconds=to_ms(now))
    minutes = max(0, int(left.total_seconds() // 60))
    return f"{minutes} dk"


__all__ = [
    "AccountView",
    "EngineEvents",
    "OrderMeta",
    "PaperEngine",
    "post_only_gate",
    "time_left_text",
]
