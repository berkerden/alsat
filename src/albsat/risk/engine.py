"""Risk motoru: yeni bir pozisyon açılabilir mi, ve limit aşıldı mı?

SPEC.md §4.6'nın tamamı burada iki soruya indirgenir:

1. **İşlem öncesi** (``evaluate``): bu emir açılabilir mi? Her kural ayrı
   bir kapıdır ve sonucu gerekçesiyle döner. Tek bir kapı kapalıysa emir
   açılmaz; kullanıcı hangi kapının neden kapalı olduğunu satır satır görür.
2. **İşlem sonrası** (``breaches``): kapanan bir işlem bir sınırı aştı mı?
   Aşıldıysa çağıran taraf otomatik işlemi kapatır (modları "Sadece Öneri"ye
   çeker) ve bildirim gönderir.

Motor **saf fonksiyondur**: hesabın anlık görüntüsünü, limitleri ve piyasa
ölçümlerini alır, karar döndürür. Diske, ağa ya da saate kendisi dokunmaz;
"şimdi" de parametredir. Bu yüzden her limit, gerçek zamanı beklemeden
test edilebilir (``tests/test_risk_motoru.py``).

Tanımlar (hepsi bütçenin yüzdesi olarak ölçülür, SPEC'in dediği gibi):

* **Günlük zarar**: İstanbul saatiyle bugün kapanan işlemlerin net toplamı
  eksideyse o tutar. Sınıra ulaşınca gün bitene kadar yeni işlem yok; yeni
  günde de otomatik işlem ancak elle açılırsa sürer.
* **Haftalık / aylık düşüş**: dönemin başındaki bakiyeden başlayıp dönem
  içinde görülen en yüksek bakiyeden sonraki en derin iniş. Bir kez sınıra
  değdiyse dönem bitene kadar değmiş sayılır.
* **Art arda kayıp**: en son işlemden geriye doğru üst üste zararla kapanan
  işlem sayısı. Kullanıcı arayüzden sıfırlayana kadar sayılır.
* **Soğuma**: bir coinde zararla kapanan işlemden sonra o işlemin
  periyodunda N mum boyunca o coinde yeni işlem yok.

"Günlük zarar" kapısı ayrıca **ileriye bakar**: bugünkü zarar + açık
pozisyonların stop zararları + bu emrin stop zararı sınırı aşıyorsa emir
açılmaz. Aksi hâlde sınırın hemen altında açılan tek bir işlem, stopa
gittiğinde sınırı bir işlem boyu aşardı.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal

from albsat.core.clock import as_utc, day_start, istanbul_text, month_start, next_day_start
from albsat.core.clock import week_start as _week_start
from albsat.core.costs import RoundTrip
from albsat.core.money import ONE_HUNDRED, ZERO
from albsat.data.klines import interval_ms
from albsat.risk.limits import RiskLimits
from albsat.risk.market import Gate, MarketState, market_gates
from albsat.risk.sizing import Binding, PositionSize, size_position

SOURCE_RULE = "kural"
SOURCE_MANUAL = "elle"

#: İşlem sonrası sınır türleri.
BREACH_DAILY = "gunluk_zarar"
BREACH_WEEKLY = "haftalik_dusus"
BREACH_MONTHLY = "aylik_dusus"
BREACH_STREAK = "art_arda_kayip"

BREACH_LABELS_TR = {
    BREACH_DAILY: "Günlük zarar sınırı",
    BREACH_WEEKLY: "Haftalık düşüş sınırı",
    BREACH_MONTHLY: "Aylık düşüş sınırı",
    BREACH_STREAK: "Art arda kayıp sınırı",
}


@dataclass(frozen=True)
class ClosedTrade:
    """Kapanmış bir işlemin risk motorunu ilgilendiren kısmı."""

    sembol: str
    periyot: str
    kapanis_utc: datetime
    net_usdt: Decimal
    kaynak: str = SOURCE_RULE
    kural_kimligi: str | None = None
    net_yuzde: float = 0.0


@dataclass(frozen=True)
class OpenExposure:
    """Açık pozisyon ya da bekleyen giriş emri."""

    sembol: str
    tutar_usdt: Decimal
    stop_zarari_usdt: Decimal
    bekleyen: bool = False


@dataclass(frozen=True)
class AccountSnapshot:
    """Kâğıt hesabının karar anındaki durumu."""

    baslangic_usdt: Decimal
    serbest_usdt: Decimal
    #: Bu hesap döneminde kapanan işlemler, eskiden yeniye.
    kapanan: tuple[ClosedTrade, ...] = ()
    acik: tuple[OpenExposure, ...] = ()
    #: Bugün (İstanbul) gönderilen giriş emri sayısı.
    girisler_bugun: int = 0
    #: Kullanıcının art arda kayıp sayacını son sıfırladığı an.
    art_arda_sifirlama_utc: datetime | None = None


@dataclass(frozen=True)
class OrderIntent:
    """Açılmak istenen emir (kural sinyali ya da elle)."""

    sembol: str
    periyot: str
    giris: Decimal
    hedef: Decimal
    stop: Decimal
    kaynak: str = SOURCE_RULE
    kural_kimligi: str | None = None


@dataclass(frozen=True)
class RiskDecision:
    """``evaluate`` sonucu: izin, kapılar ve hesaplanan büyüklük."""

    izin: bool
    kapilar: tuple[Gate, ...]
    pozisyon: PositionSize | None
    #: Hedefe giderse komisyon sonrası net yüzde.
    hedef_net_yuzde: Decimal | None = None

    @property
    def kapali_kapilar(self) -> tuple[Gate, ...]:
        return tuple(item for item in self.kapilar if not item.gecti)

    @property
    def ozet_tr(self) -> str:
        if self.izin:
            return "Bütün risk kapıları açık."
        names = ", ".join(item.etiket for item in self.kapali_kapilar)
        return f"Emir açılmadı. Kapalı kapı: {names}."


@dataclass(frozen=True)
class Breach:
    """İşlem sonrası aşılan bir sınır."""

    tur: str
    deger_yuzde: Decimal
    sinir_yuzde: Decimal
    aciklama: str

    @property
    def etiket(self) -> str:
        return BREACH_LABELS_TR.get(self.tur, self.tur)


@dataclass(frozen=True)
class RiskMetrics:
    """Arayüzün "sınıra ne kadar kaldı" göstergesi için ölçümler."""

    gunluk_net_usdt: Decimal
    gunluk_zarar_yuzde: Decimal
    haftalik_dusus_yuzde: Decimal
    aylik_dusus_yuzde: Decimal
    art_arda_kayip: int
    girisler_bugun: int
    acik_pozisyon: int
    gunluk_kalan_usdt: Decimal
    gun_sonu_utc: datetime


# --- ölçümler ---------------------------------------------------------------


def _pct_of_budget(value: Decimal, limits: RiskLimits) -> Decimal:
    if limits.butce_usdt <= ZERO:
        return ZERO
    return value / limits.butce_usdt * ONE_HUNDRED


def daily_net(snapshot: AccountSnapshot, now: datetime) -> Decimal:
    start = day_start(now)
    return sum(
        (item.net_usdt for item in snapshot.kapanan if as_utc(item.kapanis_utc) >= start),
        ZERO,
    )


def daily_loss(snapshot: AccountSnapshot, now: datetime) -> Decimal:
    net = daily_net(snapshot, now)
    return -net if net < ZERO else ZERO


def max_drawdown_since(snapshot: AccountSnapshot, start: datetime) -> Decimal:
    """``start``'tan beri gerçekleşmiş bakiyedeki en derin iniş (USDT)."""
    start = as_utc(start)
    equity = snapshot.baslangic_usdt + sum(
        (item.net_usdt for item in snapshot.kapanan if as_utc(item.kapanis_utc) < start),
        ZERO,
    )
    peak = equity
    deepest = ZERO
    for item in snapshot.kapanan:
        if as_utc(item.kapanis_utc) < start:
            continue
        equity += item.net_usdt
        peak = max(peak, equity)
        deepest = max(deepest, peak - equity)
    return deepest


def consecutive_losses(snapshot: AccountSnapshot) -> int:
    reset = snapshot.art_arda_sifirlama_utc
    count = 0
    for item in reversed(snapshot.kapanan):
        if reset is not None and as_utc(item.kapanis_utc) <= as_utc(reset):
            break
        if item.net_usdt < ZERO:
            count += 1
        else:
            break
    return count


def cooldown_until(snapshot: AccountSnapshot, sembol: str, limits: RiskLimits) -> datetime | None:
    """Coinin son işlemi zararla kapandıysa soğumanın bittiği an."""
    if limits.kayip_sonrasi_soguma_mum <= 0:
        return None
    for item in reversed(snapshot.kapanan):
        if item.sembol != sembol:
            continue
        if item.net_usdt >= ZERO:
            return None
        step = timedelta(milliseconds=interval_ms(item.periyot))
        return as_utc(item.kapanis_utc) + step * limits.kayip_sonrasi_soguma_mum
    return None


def metrics(snapshot: AccountSnapshot, limits: RiskLimits, now: datetime) -> RiskMetrics:
    loss = daily_loss(snapshot, now)
    allowed = limits.butce_usdt * limits.gunluk_max_zarar_yuzde / ONE_HUNDRED
    committed = sum((item.stop_zarari_usdt for item in snapshot.acik), ZERO)
    return RiskMetrics(
        gunluk_net_usdt=daily_net(snapshot, now),
        gunluk_zarar_yuzde=_pct_of_budget(loss, limits),
        haftalik_dusus_yuzde=_pct_of_budget(max_drawdown_since(snapshot, _week_start(now)), limits),
        aylik_dusus_yuzde=_pct_of_budget(max_drawdown_since(snapshot, month_start(now)), limits),
        art_arda_kayip=consecutive_losses(snapshot),
        girisler_bugun=snapshot.girisler_bugun,
        acik_pozisyon=len(snapshot.acik),
        gunluk_kalan_usdt=max(ZERO, allowed - loss - committed),
        gun_sonu_utc=next_day_start(now),
    )


# --- işlem öncesi kapılar ----------------------------------------------------


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def evaluate(
    intent: OrderIntent,
    *,
    snapshot: AccountSnapshot,
    limits: RiskLimits,
    market: MarketState,
    now: datetime,
    round_trip_to_target: RoundTrip,
    round_trip_to_stop: RoundTrip,
    exit_slippage_pct: Decimal,
    mode_open: bool,
    mode_text: str = "",
    disabled_rules: Sequence[str] = (),
    max_notional_usdt: Decimal | None = None,
) -> RiskDecision:
    """Bir emrin bütün risk kapılarını değerlendirir.

    ``max_notional_usdt`` verilirse emrin tutarı (fiyat × miktar) onu aşmaz: bütçe
    gibi bağlayıcı olur (Faz 6: canlı emir tavanı ve elle yazılan tutar). Bütçeyi
    daralttıysa bağlayıcı ``Binding.CAP`` yazılır; işlem başı risk yine bot bütçesinden
    hesaplanır."""
    gates: list[Gate] = []
    now = as_utc(now)

    gates.append(
        Gate(
            "mod",
            "Mod",
            mode_open,
            mode_text
            or (
                f"{intent.sembol} kâğıt işlem modunda."
                if mode_open
                else f"{intent.sembol} kâğıt işlem modunda değil; emir açılmaz."
            ),
        )
    )

    # Fiyatların kendi içinde tutarlı olması: hedef > giriş > stop.
    prices_ok = intent.hedef > intent.giris > intent.stop > ZERO
    target_net: Decimal | None = None
    if prices_ok:
        target_net = round_trip_to_target.net_margin_pct(intent.giris, intent.hedef)
        prices_ok = target_net > ZERO
        text = (
            f"Hedefe giderse komisyon sonrası net %{target_net:.4f}."
            if prices_ok
            else (
                f"Hedef maliyeti karşılamıyor: hedefe gidilse bile net "
                f"%{target_net:.4f}."
            )
        )
    else:
        text = "Fiyatlar tutarsız: hedef > giriş > stop > 0 olmalı."
    gates.append(Gate("fiyatlar", "Fiyatlar", prices_ok, text))

    if intent.kaynak == SOURCE_RULE and intent.kural_kimligi in set(disabled_rules):
        gates.append(
            Gate(
                "performans",
                "Performans bozulması",
                False,
                "Bu kural kâğıt işlemde beklentisinin anlamlı ölçüde altında kaldığı "
                "için durduruldu.",
            )
        )

    # Zarar sınırları.
    loss = daily_loss(snapshot, now)
    daily_limit = limits.butce_usdt * limits.gunluk_max_zarar_yuzde / ONE_HUNDRED
    reopen = istanbul_text(next_day_start(now))
    if loss >= daily_limit:
        gates.append(
            Gate(
                "gunluk_zarar",
                "Günlük zarar",
                False,
                f"Bugünkü zarar {_money(loss)} USDT, sınır {_money(daily_limit)} USDT. "
                f"Yeni işlem {reopen}'dan sonra ve ancak elle açılırsa.",
            )
        )
    else:
        gates.append(
            Gate(
                "gunluk_zarar",
                "Günlük zarar",
                True,
                f"Bugünkü zarar {_money(loss)} / {_money(daily_limit)} USDT.",
            )
        )

    week_dd = _pct_of_budget(max_drawdown_since(snapshot, _week_start(now)), limits)
    gates.append(
        Gate(
            "haftalik_dusus",
            "Haftalık düşüş",
            week_dd < limits.haftalik_max_dusus_yuzde,
            f"Bu hafta en derin düşüş %{week_dd:.2f}, sınır %{limits.haftalik_max_dusus_yuzde}.",
        )
    )
    month_dd = _pct_of_budget(max_drawdown_since(snapshot, month_start(now)), limits)
    gates.append(
        Gate(
            "aylik_dusus",
            "Aylık düşüş",
            month_dd < limits.aylik_max_dusus_yuzde,
            f"Bu ay en derin düşüş %{month_dd:.2f}, sınır %{limits.aylik_max_dusus_yuzde}.",
        )
    )

    streak = consecutive_losses(snapshot)
    gates.append(
        Gate(
            "art_arda_kayip",
            "Art arda kayıp",
            streak < limits.art_arda_kayip_limiti,
            f"Üst üste {streak} zarar, sınır {limits.art_arda_kayip_limiti}."
            + (
                " Sayaç arayüzden elle sıfırlanınca devam eder."
                if streak >= limits.art_arda_kayip_limiti
                else ""
            ),
        )
    )

    cool = cooldown_until(snapshot, intent.sembol, limits)
    if cool is not None and now < cool:
        gates.append(
            Gate(
                "soguma",
                "Kayıp sonrası soğuma",
                False,
                f"{intent.sembol} son işlemi zararla kapandı; {istanbul_text(cool)}'a kadar "
                "yeni işlem yok.",
            )
        )
    else:
        gates.append(Gate("soguma", "Kayıp sonrası soğuma", True, "Soğuma süresinde değil."))

    gates.append(
        Gate(
            "es_zamanli",
            "Eşzamanlı pozisyon",
            len(snapshot.acik) < limits.max_es_zamanli_pozisyon,
            f"Açık pozisyon ve bekleyen giriş: {len(snapshot.acik)}, sınır "
            f"{limits.max_es_zamanli_pozisyon}.",
        )
    )
    gates.append(
        Gate(
            "gunluk_islem",
            "Günlük işlem sayısı",
            snapshot.girisler_bugun < limits.gunluk_max_islem,
            f"Bugün {snapshot.girisler_bugun} giriş emri, sınır {limits.gunluk_max_islem}.",
        )
    )

    # Büyüklük: bütçe ile serbest bakiyenin küçüğü kullanılır.
    position: PositionSize | None = None
    budget = min(limits.butce_usdt, snapshot.serbest_usdt)
    capped = max_notional_usdt is not None and max_notional_usdt < budget
    if max_notional_usdt is not None:
        budget = min(budget, max_notional_usdt)
    if prices_ok and budget > ZERO:
        position = size_position(
            entry=intent.giris,
            stop=intent.stop,
            budget_usdt=budget,
            risk_pct=limits.butce_usdt * limits.islem_basi_risk_yuzde / budget
            if budget > ZERO
            else limits.islem_basi_risk_yuzde,
            round_trip=round_trip_to_stop,
            rules=market.kurallar,
            exit_slippage_pct=exit_slippage_pct,
            costs_in_risk=True,
        )
        if capped and position.baglayici is Binding.BUDGET:
            position = replace(position, baglayici=Binding.CAP)
    if position is None:
        gates.append(
            Gate(
                "boyut",
                "Pozisyon büyüklüğü",
                False,
                "Serbest bakiye yok." if budget <= ZERO else "Fiyatlar geçersiz.",
            )
        )
    else:
        gates.append(
            Gate(
                "boyut",
                "Pozisyon büyüklüğü",
                position.gecerli,
                position.aciklama_tr
                + (" " + " ".join(position.uyarilar) if not position.gecerli else ""),
            )
        )
        committed = sum((item.stop_zarari_usdt for item in snapshot.acik), ZERO)
        worst = loss + committed + position.stop_zarari_usdt
        gates.append(
            Gate(
                "gunluk_zarar_ileri",
                "Günlük zarar (ileriye bakış)",
                worst <= daily_limit,
                (
                    f"Bu işlem ve açık pozisyonlar stopa giderse günlük zarar "
                    f"{_money(worst)} USDT olur; sınır {_money(daily_limit)} USDT."
                ),
            )
        )
        exposure = sum(
            (item.tutar_usdt for item in snapshot.acik if item.sembol == intent.sembol), ZERO
        ) + position.tutar_usdt
        exposure_limit = limits.butce_usdt * limits.coin_basi_max_maruziyet_yuzde / ONE_HUNDRED
        gates.append(
            Gate(
                "maruziyet",
                "Coin maruziyeti",
                exposure <= exposure_limit,
                f"{intent.sembol} maruziyeti bu emirle {_money(exposure)} USDT, sınır "
                f"{_money(exposure_limit)} USDT.",
            )
        )

    gates.extend(market_gates(market, limits, now))

    allowed = all(item.gecti for item in gates)
    return RiskDecision(
        izin=allowed,
        kapilar=tuple(gates),
        pozisyon=position,
        hedef_net_yuzde=target_net,
    )


# --- işlem sonrası sınırlar --------------------------------------------------


def breaches(snapshot: AccountSnapshot, limits: RiskLimits, now: datetime) -> tuple[Breach, ...]:
    """Kapanan son işlemden sonra aşılmış sınırlar (boşsa sorun yok)."""
    found: list[Breach] = []
    loss_pct = _pct_of_budget(daily_loss(snapshot, now), limits)
    if loss_pct >= limits.gunluk_max_zarar_yuzde:
        found.append(
            Breach(
                BREACH_DAILY,
                loss_pct,
                limits.gunluk_max_zarar_yuzde,
                f"Bugünkü zarar bütçenin %{loss_pct:.2f}'i; sınır "
                f"%{limits.gunluk_max_zarar_yuzde}. Kâğıt işlem kapatıldı; yarın ancak "
                "elle açılırsa devam eder.",
            )
        )
    week_dd = _pct_of_budget(max_drawdown_since(snapshot, _week_start(now)), limits)
    if week_dd >= limits.haftalik_max_dusus_yuzde:
        found.append(
            Breach(
                BREACH_WEEKLY,
                week_dd,
                limits.haftalik_max_dusus_yuzde,
                f"Bu haftaki düşüş %{week_dd:.2f}; sınır %{limits.haftalik_max_dusus_yuzde}.",
            )
        )
    month_dd = _pct_of_budget(max_drawdown_since(snapshot, month_start(now)), limits)
    if month_dd >= limits.aylik_max_dusus_yuzde:
        found.append(
            Breach(
                BREACH_MONTHLY,
                month_dd,
                limits.aylik_max_dusus_yuzde,
                f"Bu ayki düşüş %{month_dd:.2f}; sınır %{limits.aylik_max_dusus_yuzde}.",
            )
        )
    streak = consecutive_losses(snapshot)
    if streak >= limits.art_arda_kayip_limiti:
        found.append(
            Breach(
                BREACH_STREAK,
                Decimal(streak),
                Decimal(limits.art_arda_kayip_limiti),
                f"Üst üste {streak} işlem zararla kapandı; sınır "
                f"{limits.art_arda_kayip_limiti}. Sayaç arayüzden sıfırlanana kadar "
                "yeni işlem yok.",
            )
        )
    return tuple(found)


# --- performans bozulma koruması ---------------------------------------------


@dataclass(frozen=True)
class PerformanceCheck:
    """Bir kuralın kâğıt sonuçlarının beklentisiyle kıyası (SPEC §4.6)."""

    kural_kimligi: str
    islem: int
    beklenen_yuzde: float
    gerceklesen_yuzde: float | None
    standart_hata: float | None
    z: float | None
    bozuk: bool
    aciklama: str
    ayrintilar: dict[str, float] = field(default_factory=dict)


def performance_check(
    kural_kimligi: str,
    net_pcts: Sequence[float],
    *,
    expected_pct: float,
    limits: RiskLimits,
) -> PerformanceCheck:
    """Gerçekleşen ortalama beklentinin anlamlı ölçüde altında mı?

    Tek yönlü sınama: yalnızca **kötüye** sapma kuralı durdurur. Beklentiden
    iyi gitmek bir arıza değildir (ama şans olabilir; o yüzden arayüzde
    ayrıca gösterilir). Örneklem ``performans_min_islem``'den küçükse karar
    verilmez: beş işlemle bir kuralı yargılamak gürültüyü yargılamaktır.
    """
    count = len(net_pcts)
    minimum = limits.performans_min_islem
    if count < minimum:
        return PerformanceCheck(
            kural_kimligi, count, expected_pct, None, None, None, False,
            f"{count} işlem var; sınama {minimum} işlemden sonra yapılır.",
        )
    mean = sum(net_pcts) / count
    variance = sum((value - mean) ** 2 for value in net_pcts) / (count - 1)
    error = math.sqrt(variance / count) if variance > 0 else 0.0
    if error == 0.0:
        z = 0.0 if mean >= expected_pct else -math.inf
    else:
        z = (mean - expected_pct) / error
    threshold = float(limits.performans_z_esigi)
    broken = z < -threshold
    text = (
        f"{count} işlemde ortalama net %{mean:+.4f}, beklenen %{expected_pct:+.4f} "
        f"(fark {z:+.2f} standart hata; durdurma eşiği −{threshold:.2f})."
    )
    return PerformanceCheck(
        kural_kimligi, count, expected_pct, mean, error, z, broken, text
    )


__all__ = [
    "BREACH_DAILY",
    "BREACH_LABELS_TR",
    "BREACH_MONTHLY",
    "BREACH_STREAK",
    "BREACH_WEEKLY",
    "SOURCE_MANUAL",
    "SOURCE_RULE",
    "AccountSnapshot",
    "Breach",
    "ClosedTrade",
    "OpenExposure",
    "OrderIntent",
    "PerformanceCheck",
    "RiskDecision",
    "RiskMetrics",
    "breaches",
    "consecutive_losses",
    "cooldown_until",
    "daily_loss",
    "daily_net",
    "evaluate",
    "max_drawdown_since",
    "metrics",
    "performance_check",
]
