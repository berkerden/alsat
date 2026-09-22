"""Periyot sihirbazı (SPEC.md §4.3).

Her periyot için karşılaştırma tablosu üretir: tipik mum hareketi ve bunun
gidiş-dönüş maliyete oranı, taban çizgisinin net beklenen değeri, işlem
sıklığı, al-ve-tut getirisi ve maksimum düşüşü, likidite göstergesi. Sonunda
öneri ve **gerekçesi**; son seçim kullanıcınındır.

Faz 2'den sonra sihirbazın ne söylediğini doğru anlamak önemli: bu tablo
"şu periyotta işlem yaparsan kazanırsın" demez. Söylediği şey, tipik mum
hareketinin maliyeti karşılayıp karşılamadığıdır — yani **örüntü aramanın
matematiksel olarak anlamlı olduğu** periyot hangisidir. Faz 2 bu kapsamda
aramayı yaptı ve alınacak örüntü bulamadı; o yüzden sihirbazın önerisi
"burada işlem aç" değil, "aranacaksa burada aranır" anlamına gelir. Tablonun
altındaki not bunu her seferinde yazar.

Spread bir **varsayımdır**, ölçüm değil: gerçek spread emir defterinden
okunur ve o Faz 5'in işidir. Likidite göstergesi olarak mum verisinden
okunabilen günlük kotasyon hacmi gösterilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np

from albsat.core.costs import minimum_meaningful_target
from albsat.core.money import to_decimal
from albsat.data.klines import candles_per_day, closed_only, to_utc
from albsat.data.store import KlineStore
from albsat.research.feasibility import (
    RATIO_MARGINAL,
    RATIO_UNTRADEABLE,
    FeasibilityRow,
    scan_interval,
)
from albsat.research.stats import max_drawdown_pct
from albsat.strategy.rules import RuleSet
from albsat.strategy.signals import cost_trips

#: Ölçüm için yeterli sayılan asgari mum sayısı.
MIN_CANDLES = 250


@dataclass(frozen=True)
class WizardRow:
    """Tek bir periyodun karşılaştırma satırı."""

    sembol: str
    periyot: str
    mum: int
    veri_baslangic_utc: str
    veri_bitis_utc: str

    atr_yuzde_medyan: Decimal
    minimum_hedef_yuzde: Decimal
    oran: Decimal
    sonuc: str
    aciklama: str

    gunluk_mum: float
    #: Bu periyotta kabul edilmiş kural sayısı.
    kabul_edilen_kural: int
    #: **En sık** kuralın günlük ölçülmüş sinyal sayısı. Kuralları toplamak
    #: yanıltıcı olurdu: aynı mumda birden çok kural tetiklenebilir ve
    #: toplam, gerçekte görülecek kart sayısını abartır.
    gunluk_sinyal: float
    #: Taban çizgisi: aynı dönemde uygun her muma girilseydi işlem başına net.
    taban_net_ortalama_yuzde: float
    taban_olay: int

    al_tut_net_yuzde: float
    al_tut_max_dusus_yuzde: float
    medyan_gunluk_hacim_usdt: float
    varsayilan_spread_yuzde: Decimal

    @property
    def haftalik_sinyal(self) -> float:
        return self.gunluk_sinyal * 7.0

    @property
    def uygun(self) -> bool:
        return self.oran >= RATIO_MARGINAL

    @property
    def islenebilir(self) -> bool:
        return self.oran >= RATIO_UNTRADEABLE


@dataclass(frozen=True)
class WizardResult:
    """Sihirbazın tam çıktısı: tablo, öneri, gerekçe."""

    sembol: str
    satirlar: tuple[WizardRow, ...]
    onerilen: tuple[str, ...]
    gerekce: tuple[str, ...]
    #: Kararın kullanıcıda olduğunu söyleyen kalıcı not.
    karar_notu: str = (
        "Bu tablo bir öneridir, son seçim sizindir. Birden çok periyodu "
        "birlikte de kullanabilirsiniz (üst periyot yön filtresi, alt "
        "periyot giriş zamanlaması)."
    )

    def row(self, periyot: str) -> WizardRow | None:
        for item in self.satirlar:
            if item.periyot == periyot:
                return item
        return None


def _verdict_note(row: FeasibilityRow) -> tuple[str, str]:
    return row.verdict, row.explanation_tr


def build_wizard(
    ruleset: RuleSet,
    *,
    sembol: str,
    periyotlar: tuple[str, ...],
    veri_dizini: Path | str,
) -> WizardResult:
    """Bir sembol için periyot karşılaştırma tablosunu ve öneriyi üretir."""
    store = KlineStore(veri_dizini)
    cost = ruleset.kosu.maliyet
    trip_to_target, trip_to_stop = cost_trips(cost)
    threshold = minimum_meaningful_target(
        trip_to_stop,
        spread_pct=cost.spread_yuzde,
        slippage_pct=cost.kayma_yuzde,
        safety_pct=cost.guvenlik_payi_yuzde,
    )

    rows: list[WizardRow] = []
    for periyot in periyotlar:
        frame = store.read(sembol, periyot)
        if frame.empty:
            continue
        frame = closed_only(frame).reset_index(drop=True)
        if len(frame) < MIN_CANDLES:
            continue

        feasibility = scan_interval(
            frame, symbol=sembol, interval=periyot, threshold=threshold
        )
        verdict, note = _verdict_note(feasibility)

        close = frame["close"].to_numpy(dtype=float)
        start = float(frame["open"].iloc[0])
        end = float(close[-1])
        gross = (end - start) / start * 100.0 if start else 0.0
        fee = float(trip_to_stop.total_fee_pct)
        drawdown = max_drawdown_pct(close)

        per_day = candles_per_day(periyot)
        volume = _median_daily_quote_volume(frame, per_day)

        sections = ruleset.sections_for(sembol, periyot)
        accepted = sum(item.kabul_edilen for item in sections)
        baseline_mean = (
            float(np.mean([item.taban_net_ortalama_yuzde for item in sections]))
            if sections
            else 0.0
        )
        baseline_events = max((item.taban_olay for item in sections), default=0)

        rules_here = ruleset.for_symbol(sembol, periyot)
        daily_signals = _daily_signal_rate(rules_here, frame_len=len(frame), per_day=per_day)

        rows.append(
            WizardRow(
                sembol=sembol,
                periyot=periyot,
                mum=int(len(frame)),
                veri_baslangic_utc=to_utc(int(frame["open_time"].iloc[0])).isoformat(),
                veri_bitis_utc=to_utc(int(frame["open_time"].iloc[-1])).isoformat(),
                atr_yuzde_medyan=feasibility.atr_pct_median,
                minimum_hedef_yuzde=feasibility.minimum_target_pct,
                oran=feasibility.ratio,
                sonuc=verdict,
                aciklama=note,
                gunluk_mum=per_day,
                kabul_edilen_kural=accepted,
                gunluk_sinyal=daily_signals,
                taban_net_ortalama_yuzde=baseline_mean,
                taban_olay=baseline_events,
                al_tut_net_yuzde=gross - fee,
                al_tut_max_dusus_yuzde=drawdown,
                medyan_gunluk_hacim_usdt=volume,
                varsayilan_spread_yuzde=to_decimal(cost.spread_yuzde),
            )
        )

    return WizardResult(
        sembol=sembol,
        satirlar=tuple(rows),
        onerilen=_recommend(rows),
        gerekce=_reasons(rows, ruleset),
    )


def _median_daily_quote_volume(frame, per_day: float) -> float:
    """Günlük kotasyon hacminin medyanı (likidite göstergesi)."""
    if "quote_volume" not in frame.columns or per_day <= 0:
        return 0.0
    values = frame["quote_volume"].to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0
    return float(np.median(values) * per_day)


def _daily_signal_rate(rules, *, frame_len: int, per_day: float) -> float:
    """En sık tetiklenen kuralın günlük ölçülmüş sinyal sayısı.

    Kuralın ölçülmüş olay sayısı, ölçüldüğü serinin uzunluğuna bölünür.
    Kural yoksa sıfırdır — ve sıfır, tahmin değil ölçümdür.
    """
    if not rules or frame_len <= 0 or per_day <= 0:
        return 0.0
    busiest = max(rule.kanit.olay for rule in rules)
    return busiest / frame_len * per_day


def _recommend(rows: list[WizardRow]) -> tuple[str, ...]:
    suitable = [row for row in rows if row.uygun]
    if suitable:
        suitable.sort(key=lambda row: row.oran, reverse=True)
        return tuple(row.periyot for row in suitable)
    marginal = [row for row in rows if row.islenebilir]
    marginal.sort(key=lambda row: row.oran, reverse=True)
    return tuple(row.periyot for row in marginal[:1])


def _reasons(rows: list[WizardRow], ruleset: RuleSet) -> tuple[str, ...]:
    if not rows:
        return ("Karşılaştırma için yeterli veri yok.",)

    lines: list[str] = []
    best = max(rows, key=lambda row: row.oran)
    lines.append(
        f"En yüksek oran {best.periyot} periyodunda: tipik mum hareketi "
        f"(%{best.atr_yuzde_medyan:.4f}) maliyet eşiğinin "
        f"({best.minimum_hedef_yuzde:.4f}%) {best.oran:.2f} katı."
    )

    weak = [row for row in rows if not row.islenebilir]
    if weak:
        lines.append(
            "Oranı 1'in altında kalan periyotlarda tipik hareket maliyeti bile "
            "karşılamıyor: " + ", ".join(row.periyot for row in weak) + "."
        )

    accepted_total = ruleset.kabul_edilen_sayisi
    if accepted_total == 0:
        lines.append(
            "Bu tablo nerede ARANACAĞINI söyler, nerede kazanılacağını değil. "
            f"Tarama {ruleset.kosu.toplam_aday:,} aday denedi ve kabul edilen "
            "örüntü çıkmadı; bu yüzden hangi periyot seçilirse seçilsin öneri "
            "motoru şu an kart üretmez."
        )
    else:
        lines.append(
            f"Kabul edilmiş {accepted_total} kural var; öneri motoru bunları "
            "seçtiğiniz periyotta uygular."
        )

    lines.append(
        "Al-ve-tut sütunu bir strateji önerisi değil, ölçülen dönemdeki "
        "karşılaştırma ölçütüdür; yanındaki maksimum düşüş o getirinin "
        "bedelini gösterir."
    )
    lines.append(
        "Spread sütuna varsayım olarak girer (gerçek spread emir defterinden "
        "ölçülür); likidite göstergesi mum verisinden okunan günlük kotasyon "
        "hacmidir."
    )
    return tuple(lines)


def render_table(result: WizardResult) -> str:
    """Sihirbaz tablosunu düz metin olarak üretir (komut satırı ve rapor için)."""
    if not result.satirlar:
        return "Karşılaştırma için yeterli veri yok."

    header = (
        f"{'Periyot':>7} {'Mum':>9} {'ATR% medyan':>12} {'Min hedef%':>11} "
        f"{'Oran':>6} {'Sinyal/gün':>11} {'Taban net%':>11} "
        f"{'Al-tut net%':>12} {'Maks düşüş%':>12}  Sonuç"
    )
    lines = [f"{result.sembol} — periyot karşılaştırması", "", header, "-" * len(header)]
    for row in result.satirlar:
        lines.append(
            f"{row.periyot:>7} {row.mum:>9,} {row.atr_yuzde_medyan:>12.4f} "
            f"{row.minimum_hedef_yuzde:>11.4f} {row.oran:>6.2f} "
            f"{row.gunluk_sinyal:>11.2f} {row.taban_net_ortalama_yuzde:>11.4f} "
            f"{row.al_tut_net_yuzde:>12.2f} {row.al_tut_max_dusus_yuzde:>12.2f}  "
            f"{row.sonuc}"
        )
    lines.append("")
    if result.onerilen:
        lines.append("Önerilen: " + ", ".join(result.onerilen))
    else:
        lines.append("Önerilen: yok — hiçbir periyot maliyet eşiğini geçmiyor.")
    lines.append("")
    lines.extend(f"- {item}" for item in result.gerekce)
    lines.append("")
    lines.append(result.karar_notu)
    return "\n".join(lines)


__all__ = [
    "MIN_CANDLES",
    "WizardResult",
    "WizardRow",
    "build_wizard",
    "render_table",
]
