"""Fizibilite taraması: "bu coin + bu periyot matematiksel olarak anlamlı mı?"

FAZ0-MIMARI.md Risk #5'in karşılığı ve Faz 1'in ilk çıktısı.

Mantık basit ve acımasız: bir periyotta tipik mum hareketi, gidiş-dönüş
maliyetini (komisyon + spread + kayma + güvenlik payı) karşılamıyorsa o
periyotta örüntü aramanın matematiksel bir anlamı yoktur. Bunu örüntü
motorunu kurmadan **önce** ölçmek, sonra öğrenmekten çok daha ucuzdur.

Ölçülen temel büyüklük::

    fizibilite oranı = ATR%  /  minimum anlamlı hedef %

* oran < 1.0 → tipik mum, maliyeti bile karşılamıyor. Bu periyotta işlem yok.
* 1.0 ≤ oran < 2.0 → sınırda. Ancak çok isabetli bir sinyalle anlamlı olur.
* oran ≥ 2.0 → hareket maliyetin belirgin üstünde; örüntü aramaya değer.

Bu eşikler *varsayılan* değerlerdir, yargı değil. Rapor her zaman ham
sayıları da gösterir.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from albsat.core.costs import TargetThreshold
from albsat.core.money import ONE_HUNDRED, ZERO, to_decimal
from albsat.data.klines import candles_per_day, closed_only

#: Fizibilite oranı eşikleri (varsayılan).
RATIO_UNTRADEABLE = Decimal("1.0")
RATIO_MARGINAL = Decimal("2.0")


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Wilder'ın gerçek aralığı (true range).

    ``max(high-low, |high-önceki kapanış|, |low-önceki kapanış|)``
    """
    high, low, close = frame["high"], frame["low"], frame["close"]
    previous_close = close.shift(1)
    spans = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    )
    return spans.max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder yumuşatmalı ATR (``ewm(alpha=1/period)`` ile eşdeğer)."""
    return true_range(frame).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def atr_percent(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR'nin kapanış fiyatına oranı, yüzde olarak."""
    return atr(frame, period) / frame["close"] * 100.0


@dataclass(frozen=True)
class FeasibilityRow:
    """Tek bir sembol + periyot için fizibilite sonucu."""

    symbol: str
    interval: str
    candles: int
    atr_pct_median: Decimal
    atr_pct_p25: Decimal
    atr_pct_p75: Decimal
    range_pct_median: Decimal
    threshold: TargetThreshold
    candles_per_day: float

    @property
    def minimum_target_pct(self) -> Decimal:
        return self.threshold.minimum_target_pct

    @property
    def ratio(self) -> Decimal:
        """Medyan ATR% / minimum anlamlı hedef %."""
        if self.minimum_target_pct <= ZERO:
            return ZERO
        return self.atr_pct_median / self.minimum_target_pct

    @property
    def verdict(self) -> str:
        if self.candles == 0:
            return "VERİ YOK"
        if self.ratio < RATIO_UNTRADEABLE:
            return "UYGUN DEĞİL"
        if self.ratio < RATIO_MARGINAL:
            return "SINIRDA"
        return "UYGUN"

    @property
    def explanation_tr(self) -> str:
        if self.candles == 0:
            return "Bu periyot için kapanmış mum verisi yok."
        if self.ratio < RATIO_UNTRADEABLE:
            return (
                f"Tipik mum hareketi (%{self.atr_pct_median:.4f}) gidiş-dönüş "
                f"maliyetini (%{self.minimum_target_pct:.4f}) karşılamıyor. "
                "Bu periyotta işlem açmak matematiksel olarak zararına."
            )
        if self.ratio < RATIO_MARGINAL:
            return (
                f"Tipik hareket maliyetin {self.ratio:.2f} katı. Marj dar; "
                "ancak çok yüksek isabetli bir sinyalle anlamlı olabilir."
            )
        return (
            f"Tipik hareket maliyetin {self.ratio:.2f} katı. Örüntü aramaya "
            "elverişli."
        )


def scan_interval(
    frame: pd.DataFrame,
    *,
    symbol: str,
    interval: str,
    threshold: TargetThreshold,
    atr_period: int = 14,
) -> FeasibilityRow:
    """Tek bir sembol + periyot için fizibilite satırı üretir.

    Yalnızca **kapanmış** mumlar kullanılır.
    """
    frame = closed_only(frame)
    if len(frame) <= atr_period:
        return FeasibilityRow(
            symbol=symbol,
            interval=interval,
            candles=int(len(frame)),
            atr_pct_median=ZERO,
            atr_pct_p25=ZERO,
            atr_pct_p75=ZERO,
            range_pct_median=ZERO,
            threshold=threshold,
            candles_per_day=candles_per_day(interval),
        )

    series = atr_percent(frame, atr_period).dropna()
    bar_range = ((frame["high"] - frame["low"]) / frame["close"] * 100.0).dropna()

    def dec(value: float) -> Decimal:
        return to_decimal(f"{float(value):.8f}")

    return FeasibilityRow(
        symbol=symbol,
        interval=interval,
        candles=int(len(frame)),
        atr_pct_median=dec(np.median(series)),
        atr_pct_p25=dec(np.percentile(series, 25)),
        atr_pct_p75=dec(np.percentile(series, 75)),
        range_pct_median=dec(np.median(bar_range)),
        threshold=threshold,
        candles_per_day=candles_per_day(interval),
    )


def render_table(rows: list[FeasibilityRow]) -> str:
    """SPEC.md §4.3'ün istediği karşılaştırma tablosunu Türkçe metin olarak üretir."""
    if not rows:
        return "Tablo için veri yok."

    header = (
        f"{'Sembol':<10} {'Periyot':>7} {'Mum':>9} {'ATR% ort':>9} "
        f"{'Min hedef%':>11} {'Oran':>6} {'Gün/mum':>9}  Sonuç"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.symbol:<10} {row.interval:>7} {row.candles:>9,} "
            f"{row.atr_pct_median:>9.4f} {row.minimum_target_pct:>11.4f} "
            f"{row.ratio:>6.2f} {row.candles_per_day:>9,.0f}  {row.verdict}"
        )
    lines.append("")
    lines.append(
        "Oran = tipik mum hareketi / maliyeti karşılayan en küçük hedef. "
        "1'in altı, o periyotta işlemin matematiksel olarak zararına olduğu anlamına gelir."
    )
    return "\n".join(lines)
