"""Zaman bölmeleri, walk-forward ve kararlılık (SPEC.md §4.2).

Bir örüntünün tüm veride iyi görünmesi hiçbir şey kanıtlamaz: yeterince
örüntü denenirse biri mutlaka iyi görünür. Anlamlı soru şudur: **veriyi
görmeden önce seçilmiş olsaydı, sonrasında da çalışır mıydı?**

İki araç:

* **Eğitim / doğrulama / test** bölmesi — zamana göre, karıştırmadan.
  Karıştırmak (shuffle) finansal seride en pahalı hatadır: geleceği eğitim
  kümesine sızdırır.
* **Walk-forward** — genişleyen pencere. Her katmanda "o güne kadarki
  veriyle seç, sonraki dönemde ölç" yapılır; gerçek kullanımın provası budur.

Ayrıca **kararlılık**: örüntünün çeyreklik bazda nasıl gittiği. Bir yıl
çalışıp sonra bozulan örüntü, ortalamaya bakıldığında hâlâ iyi görünür.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Split:
    """Bir zaman dilimi (yarı açık aralık: ``start`` dahil, ``stop`` hariç)."""

    name: str
    start: int
    stop: int

    def __len__(self) -> int:
        return max(0, self.stop - self.start)

    def mask(self, count: int) -> np.ndarray:
        out = np.zeros(count, dtype=bool)
        out[self.start : self.stop] = True
        return out


def train_validation_test(
    count: int, *, fractions: tuple[float, float, float] = (0.5, 0.25, 0.25)
) -> tuple[Split, Split, Split]:
    """Veriyi zamana göre üçe böler. Karıştırma yok, sızma yok."""
    if count <= 0:
        empty = Split("", 0, 0)
        return empty, empty, empty
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError("oranların toplamı 1 olmalı")

    first = int(count * fractions[0])
    second = first + int(count * fractions[1])
    return (
        Split("eğitim", 0, first),
        Split("doğrulama", first, second),
        Split("test", second, count),
    )


def walk_forward_splits(
    count: int, *, folds: int = 4, min_train_fraction: float = 0.4
) -> list[tuple[Split, Split]]:
    """Genişleyen pencereli walk-forward katmanları.

    Her katman ``(eğitim, test)`` çiftidir; eğitim her zaman testin
    **öncesindedir** ve katman ilerledikçe büyür.
    """
    if count <= 0 or folds < 1:
        return []
    start = int(count * min_train_fraction)
    if start < 1 or start >= count:
        return []

    step = (count - start) / folds
    if step < 1:
        return []

    layers: list[tuple[Split, Split]] = []
    for index in range(folds):
        train_end = int(start + step * index)
        test_end = int(start + step * (index + 1)) if index < folds - 1 else count
        if test_end <= train_end:
            continue
        layers.append(
            (
                Split(f"eğitim {index + 1}", 0, train_end),
                Split(f"test {index + 1}", train_end, test_end),
            )
        )
    return layers


@dataclass(frozen=True)
class StabilityRow:
    """Bir dönemdeki örüntü performansı."""

    period: str
    events: int
    net_mean_pct: float
    hit_rate: float

    @property
    def positive(self) -> bool:
        return self.net_mean_pct > 0.0


def stability_by_period(
    open_time: np.ndarray,
    net_pct: np.ndarray,
    hit: np.ndarray,
    mask: np.ndarray,
    *,
    frequency: str = "QE",
) -> list[StabilityRow]:
    """Örüntüyü dönemlere bölüp her dönemde ayrı ayrı ölçer.

    Varsayılan çeyreklik. Bir örüntünün dönemlerin çoğunda artıda olması,
    toplamda artıda olmasından daha güçlü bir kanıttır.
    """
    selected = np.flatnonzero(mask)
    if selected.size == 0:
        return []

    stamps = pd.to_datetime(pd.Series(open_time[selected]), unit="ms", utc=True)
    table = pd.DataFrame(
        {"net": net_pct[selected], "hit": hit[selected]}, index=pd.DatetimeIndex(stamps)
    ).dropna(subset=["net"])
    if table.empty:
        return []

    rows: list[StabilityRow] = []
    for period, group in table.resample(frequency):
        if group.empty:
            continue
        rows.append(
            StabilityRow(
                period=str(pd.Timestamp(period).date()),
                events=int(len(group)),
                net_mean_pct=float(group["net"].mean()),
                hit_rate=float(group["hit"].mean()),
            )
        )
    return rows


def positive_period_share(rows: list[StabilityRow]) -> float:
    """Dönemlerin kaçta kaçında örüntü artıda? (0–1)"""
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.positive) / len(rows)
