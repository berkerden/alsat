"""Öneri kartı: SPEC.md §4.4'teki alanların hesaplanması.

Kartın tek işi, kabul edilmiş bir kuralın son kapanmış mumda tetiklenmesi
hâlinde kullanıcının karar verebilmesi için gereken **her sayıyı** ve o
sayının nereden geldiğini üretmek. Kâr vaat etmez; "şu fiyattan alırsan,
komisyon sonrası eline şu geçer, ters giderse şu kadar kaybedersin" der.

Üç tasarım kararı burada sabitlenmiştir:

1. **Kart, kuralın kabul edildiği maliyet varsayımlarını kullanır.** Kartın
   marjı, kuralın kanıtından farklı bir maliyetle hesaplanırsa kart kuralın
   kanıtıyla çelişir. Gerçek komisyon oranı Faz 4'te ölçülünce tarama da
   kart da o oranla yenilenir.

2. **Giriş bir limit fiyatıdır ama araştırma piyasa emri varsaydı.** Olay
   çalışmasında giriş, sinyal mumundan **sonraki** mumun açılışıdır ve taker
   komisyonu ödenir (bkz. ``eventstudy``). Canlıda o açılış henüz
   bilinmediği için kart, son kapanışı ``tickSize``'a yuvarlayarak limit
   giriş önerir. İki varsayımın farkı kartın üstünde yazar; gizlenmez.

3. **Hedef 2 uydurulmaz.** Kademeli kâr alma seviyesi, örüntünün ölçülmüş
   medyan MFE'sinden (pozisyon süresince görülen en iyi fiyat) türetilir.
   Ölçülen en iyi fiyat Hedef 1'in altındaysa Hedef 2 **yoktur** ve bu
   yazılır. "Biraz daha yukarısı" diye bir seviye koymak, ölçülmemiş bir
   sayıyı ölçülmüş gibi göstermek olurdu.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from albsat.core.costs import RoundTrip
from albsat.core.fees import Side
from albsat.core.filters import SymbolRules
from albsat.core.money import (
    ONE_HUNDRED,
    ZERO,
    Number,
    Rounding,
    clamp_decimals,
    to_decimal,
)
from albsat.risk.sizing import PositionSize
from albsat.strategy.rules import Rule, RuleEvidence

#: İstanbul saati UTC+3; 2016'dan beri yaz saati uygulaması yok.
ISTANBUL = timezone(timedelta(hours=3))

ENTRY_ASSUMPTION_NOTE = (
    "Kuralın istatistikleri, sinyal mumundan sonraki mumun açılışında "
    "PİYASA emriyle girildiği varsayımıyla ölçüldü. Kart ise son kapanışa "
    "limit emir öneriyor: limit dolmazsa işlem hiç açılmaz, dolarsa fiyat "
    "ölçülenden farklı olabilir. İki varsayım aynı değildir."
)

DISCLAIMER = (
    "Öneriler geçmiş verilere dayalı istatistiksel çıkarımlardır, yatırım "
    "tavsiyesi değildir; geçmiş performans geleceği garanti etmez."
)


@dataclass(frozen=True)
class ConfidenceComponent:
    """Güven skorunun tek bir bileşeni; kartta satır satır gösterilir."""

    ad: str
    puan: float
    azami: float
    aciklama: str


@dataclass(frozen=True)
class ConfidenceScore:
    """0–100 arası güven skoru ve **nasıl hesaplandığı** (SPEC §4.4).

    Skor bir olasılık değildir; kabul edilmiş bir kuralın kanıtının ne kadar
    sağlam olduğunu tek sayıya indirir. Bileşenler her zaman birlikte
    gösterilir, çünkü tek başına "72" hiçbir şey anlatmaz.
    """

    puan: int
    bilesenler: tuple[ConfidenceComponent, ...]
    ceza: float = 0.0

    @property
    def seviye_tr(self) -> str:
        if self.puan >= 75:
            return "yüksek"
        if self.puan >= 50:
            return "orta"
        return "düşük"

    @property
    def aciklama_tr(self) -> str:
        parts = [f"{item.ad}: {item.puan:.0f}/{item.azami:.0f}" for item in self.bilesenler]
        text = f"{self.puan}/100 ({self.seviye_tr}) — " + ", ".join(parts)
        if self.ceza:
            text += f", uyarı cezası −{self.ceza:.0f}"
        return text


def confidence_for(rule: Rule, *, min_events: int = 50) -> ConfidenceScore:
    """Kuralın kanıtından güven skorunu üretir."""
    evidence = rule.kanit
    components: list[ConfidenceComponent] = []

    sample_ratio = min(1.0, evidence.kabul_ornegi / max(min_events, 1))
    components.append(
        ConfidenceComponent(
            "Bağımsız örnek",
            20.0 * sample_ratio,
            20.0,
            f"Kabul kararı {evidence.kabul_ornegi} üst üste binmeyen olaya "
            f"dayanıyor (eşik {min_events}).",
        )
    )

    # q-değeri eşiğin ne kadar altında? Eşitse 0, onda biriyse tam puan.
    if evidence.kabul_esigi_p > 0 and evidence.p_degeri > 0:
        margin = evidence.kabul_esigi_p / evidence.p_degeri
        strength = min(1.0, max(0.0, (margin - 1.0) / 9.0))
    else:
        strength = 0.0
    components.append(
        ConfidenceComponent(
            "İstatistiksel pay",
            20.0 * strength,
            20.0,
            f"Ham p={evidence.p_degeri:.6f}, kabul eşiği "
            f"{evidence.kabul_esigi_p:.6f}; q={evidence.q_degeri:.4f}.",
        )
    )

    components.append(
        ConfidenceComponent(
            "Dönem kararlılığı",
            20.0 * max(0.0, min(1.0, evidence.donem_dogru_yon_orani)),
            20.0,
            f"Çeyreklerin %{evidence.donem_dogru_yon_orani * 100:.0f}'inde "
            "doğru yönde.",
        )
    )

    components.append(
        ConfidenceComponent(
            "Walk-forward",
            20.0 * max(0.0, min(1.0, evidence.walk_forward_dogru_yon_orani)),
            20.0,
            f"Katmanların %{evidence.walk_forward_dogru_yon_orani * 100:.0f}'inde "
            f"doğru yönde (ortalama %{evidence.walk_forward_ortalama_yuzde:+.4f}).",
        )
    )

    components.append(
        ConfidenceComponent(
            "Rastgele kıyası",
            10.0 if evidence.rastgeleyi_geciyor else 0.0,
            10.0,
            f"Rastgele girişlerin %{evidence.rastgele_yuzdelik * 100:.0f}'ini geçiyor.",
        )
    )

    correct_direction = (
        evidence.test_donemi_net_yuzde > 0
        if rule.yon == "al"
        else evidence.test_donemi_net_yuzde < 0
    )
    components.append(
        ConfidenceComponent(
            "Test dönemi",
            10.0 if correct_direction else 0.0,
            10.0,
            f"Ayrılmış test döneminde net %{evidence.test_donemi_net_yuzde:+.4f}.",
        )
    )

    penalty = 5.0 * len(rule.uyarilar)
    total = max(0.0, sum(item.puan for item in components) - penalty)
    return ConfidenceScore(
        puan=int(round(total)), bilesenler=tuple(components), ceza=penalty
    )


@dataclass(frozen=True)
class SignalCard:
    """SPEC.md §4.4'ün istediği tüm alanlarıyla tek bir öneri."""

    sembol: str
    periyot: str
    kural_kimligi: str
    kural_etiketi: str
    #: AL / SAT / BEKLE.
    aksiyon: str
    aksiyon_aciklama: str

    sinyal_mumu_kapanis_utc: str
    gecerlilik_mum: int
    gecerlilik_bitis_utc: str

    giris: Decimal
    hedef1: Decimal
    hedef2: Decimal | None
    stop: Decimal
    basa_bas: Decimal

    brut_marj_yuzde: Decimal
    net_marj_yuzde: Decimal
    hedef2_net_marj_yuzde: Decimal | None
    stop_net_marj_yuzde: Decimal
    risk_odul: Decimal | None

    pozisyon: PositionSize
    maliyet_ozeti: str
    guven: ConfidenceScore
    gerekce: tuple[str, ...]
    uyarilar: tuple[str, ...]
    #: ``exchangeInfo`` önbellekte yoksa fiyatlar yuvarlanmamıştır.
    filtreler_uygulandi: bool
    #: Kuralın kanıtı olduğu gibi taşınır. SPEC §4.4 kartta "tarihsel isabet
    #: oranı (n=...)" istiyor; bu sayıyı kart yeniden hesaplamaz, kuralın
    #: kabul edildiği koşudan gelen değeri gösterir.
    kanit: RuleEvidence
    hedef2_notu: str = ""
    try_kuru: Decimal | None = None

    @property
    def gecerlilik_bitis_istanbul(self) -> str:
        return _istanbul_text(self.gecerlilik_bitis_utc)

    @property
    def sinyal_mumu_istanbul(self) -> str:
        return _istanbul_text(self.sinyal_mumu_kapanis_utc)

    def try_of(self, value: Decimal | None) -> Decimal | None:
        """USDT tutarının TRY karşılığı; kur verilmemişse ``None``."""
        if value is None or self.try_kuru is None:
            return None
        return (value * self.try_kuru).quantize(Decimal("0.01"))


def _istanbul_text(iso_utc: str) -> str:
    try:
        moment = datetime.fromisoformat(iso_utc)
    except ValueError:
        return iso_utc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ISTANBUL).strftime("%d.%m.%Y %H:%M")


def _round_price(value: Decimal, rules: SymbolRules | None, side: Side) -> Decimal:
    """Fiyatı borsanın adımına yuvarlar.

    Filtreler elde yoksa adım **uydurulmaz**; yalnızca ``Decimal`` bölmesinin
    ürettiği 28 basamak, Binance'in en fazla kabul ettiği 8 basamağa
    indirilir ve yön aleyhimize seçilir (alış aşağı, satış yukarı). Kartın
    üstünde "filtreler uygulanmadı" uyarısı ayrıca durur.
    """
    if rules is None:
        mode = Rounding.FLOOR if side is Side.BUY else Rounding.CEILING
        return clamp_decimals(value, mode=mode)
    return rules.round_price(value, side)


def build_card(
    rule: Rule,
    *,
    close_price: Number,
    atr_pct: Number,
    signal_close_time_utc: str,
    interval_ms: int,
    trip_to_target: RoundTrip,
    trip_to_stop: RoundTrip,
    position: PositionSize,
    rules: SymbolRules | None,
    feature_descriptions: dict[str, str] | None = None,
    min_events: int = 50,
    try_rate: Number | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> SignalCard:
    """Tetiklenmiş bir kuraldan öneri kartını üretir.

    ``atr_pct`` sinyal mumundaki ATR yüzdesidir; hedef ve stop mesafeleri
    kuralın ATR katsayılarıyla buradan çıkar. Sabit yüzde kullanmak sakin ve
    oynak dönemleri aynı sayardı (bkz. ``eventstudy.OutcomeConfig``).
    """
    entry_raw = to_decimal(close_price)
    atr = to_decimal(atr_pct)

    target_pct = atr * to_decimal(f"{rule.hedef_atr:g}")
    stop_pct = atr * to_decimal(f"{rule.stop_atr:g}")

    entry = _round_price(entry_raw, rules, Side.BUY)
    target1 = _round_price(entry * (ONE_HUNDRED + target_pct) / ONE_HUNDRED, rules, Side.SELL)
    stop = _round_price(entry * (ONE_HUNDRED - stop_pct) / ONE_HUNDRED, rules, Side.BUY)
    break_even = _round_price(trip_to_target.break_even_price(entry), rules, Side.SELL)

    # Hedef 2 ölçülmüş MFE medyanından gelir; ölçülen en iyi fiyat Hedef 1'in
    # altındaysa ikinci hedef yoktur.
    mfe_pct = to_decimal(f"{rule.kanit.mfe_medyan_yuzde:.6f}")
    target2: Decimal | None = None
    target2_note = (
        "Ölçülen medyan en iyi fiyat (MFE) Hedef 1'in altında kaldığı için "
        "ikinci hedef konmadı. Kademeli çıkış için ölçülmüş bir dayanak yok."
    )
    target2_net: Decimal | None = None
    if mfe_pct > target_pct:
        target2 = _round_price(
            entry * (ONE_HUNDRED + mfe_pct) / ONE_HUNDRED, rules, Side.SELL
        )
        target2_net = trip_to_target.net_margin_pct(entry, target2)
        target2_note = (
            f"Hedef 2, örüntünün ölçülmüş medyan en iyi fiyatıdır "
            f"(MFE %{rule.kanit.mfe_medyan_yuzde:+.4f}). Olayların yarısı bu "
            "seviyeyi gördü; yarısı görmedi."
        )

    gross = (target1 - entry) / entry * ONE_HUNDRED if entry > ZERO else ZERO
    net = trip_to_target.net_margin_pct(entry, target1)
    stop_net = trip_to_stop.net_margin_pct(entry, stop)

    risk_reward: Decimal | None = None
    if stop_net < ZERO and net > ZERO:
        risk_reward = (net / -stop_net).quantize(Decimal("0.01"))

    expiry = _expiry_text(signal_close_time_utc, interval_ms, rule.pencere_mum)

    descriptions = feature_descriptions or {}
    reasons = [descriptions.get(name, name) for name in rule.ozellikler]
    reasons.append(
        f"Hedef {rule.hedef_atr:g}×ATR, stop {rule.stop_atr:g}×ATR; sinyal "
        f"mumunda ATR %{atr:.4f}."
    )
    reasons.append(ENTRY_ASSUMPTION_NOTE)

    warnings = list(rule.uyarilar) + list(extra_warnings)
    if rules is None:
        warnings.append(
            "Sembol filtreleri (tickSize/stepSize/NOTIONAL) elde yok; fiyatlar "
            "borsaya gönderilebilir biçime yuvarlanmadı. Veri tazeleme adımını "
            "çalıştırın."
        )
    warnings.extend(position.uyarilar)

    action = "AL" if rule.yon == "al" else "SAT"
    action_text = (
        "Alım önerisi."
        if rule.yon == "al"
        else (
            "Kaçınma sinyali. Spot'ta açığa satış yok; bu sinyal 'yeni alım "
            "yapma' veya 'elindekini sat' anlamına gelir."
        )
    )

    return SignalCard(
        sembol=rule.sembol,
        periyot=rule.periyot,
        kural_kimligi=rule.kimlik,
        kural_etiketi=rule.etiket,
        aksiyon=action,
        aksiyon_aciklama=action_text,
        sinyal_mumu_kapanis_utc=signal_close_time_utc,
        gecerlilik_mum=rule.pencere_mum,
        gecerlilik_bitis_utc=expiry,
        giris=entry,
        hedef1=target1,
        hedef2=target2,
        stop=stop,
        basa_bas=break_even,
        brut_marj_yuzde=gross,
        net_marj_yuzde=net,
        hedef2_net_marj_yuzde=target2_net,
        stop_net_marj_yuzde=stop_net,
        risk_odul=risk_reward,
        pozisyon=position,
        maliyet_ozeti=(
            f"Giriş {trip_to_target.entry.liquidity.value.lower()} "
            f"%{trip_to_target.entry.rate_pct:.4f}, hedefe çıkış "
            f"{trip_to_target.exit.liquidity.value.lower()} "
            f"%{trip_to_target.exit.rate_pct:.4f}, stopa çıkış "
            f"{trip_to_stop.exit.liquidity.value.lower()} "
            f"%{trip_to_stop.exit.rate_pct:.4f}."
        ),
        guven=confidence_for(rule, min_events=min_events),
        gerekce=tuple(reasons),
        uyarilar=tuple(warnings),
        filtreler_uygulandi=rules is not None,
        kanit=rule.kanit,
        hedef2_notu=target2_note,
        try_kuru=to_decimal(try_rate) if try_rate is not None else None,
    )


def _expiry_text(signal_close_time_utc: str, interval_ms: int, bars: int) -> str:
    try:
        moment = datetime.fromisoformat(signal_close_time_utc)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (
        moment + timedelta(milliseconds=interval_ms * max(bars, 1))
    ).replace(microsecond=0).isoformat()


__all__ = [
    "DISCLAIMER",
    "ENTRY_ASSUMPTION_NOTE",
    "ConfidenceComponent",
    "ConfidenceScore",
    "SignalCard",
    "build_card",
    "confidence_for",
]
