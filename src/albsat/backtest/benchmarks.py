"""Kıyas ölçütleri: rastgele giriş ve al-ve-tut (SPEC.md §4.2, Faz 2 kabul kriteri).

Bir örüntünün "kâr ediyor" olması tek başına bir şey söylemez. Doğru soru:
**aynı piyasada rastgele girip aynı hedef-stop kurallarını uygulasaydık ne
olurdu?** Piyasa o dönemde yükseldiyse rastgele giriş de kazandırır; örüntü
ancak rastgeleyi aşarsa bir şey eklemiş olur.

İki ayrı kıyas var, ikisi de gerekli:

* **Rastgele giriş** — aynı sayıda işlem, aynı hedef/stop, rastgele mumlarda.
  Örüntünün *zamanlaması* bir şey katıyor mu?
* **Al-ve-tut** — dönem başında alıp sonunda satmak. Örüntü, hiçbir şey
  yapmamaktan iyi mi?

Faz 2'nin kabul kriteri raporun bu kıyasları içermesidir.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from albsat.backtest.engine import BacktestResult, run
from albsat.core.costs import RoundTrip
from albsat.research.eventstudy import OutcomeTable, net_margin_pct_array


@dataclass(frozen=True)
class RandomComparison:
    """Örüntünün rastgele girişe göre yeri."""

    pattern_mean_pct: float
    random_mean_pct: float
    random_p05_pct: float
    random_p95_pct: float
    percentile: float
    repeats: int
    sample_size: int

    @property
    def beats_random(self) -> bool:
        """Rastgele girişlerin %95'inden iyi mi?"""
        return self.percentile >= 0.95

    @property
    def summary_tr(self) -> str:
        if self.sample_size == 0:
            return "Kıyas için yeterli olay yok."
        verdict = (
            "rastgele girişi geçiyor"
            if self.beats_random
            else "rastgele girişten ayırt edilemiyor"
        )
        return (
            f"Örüntü %{self.pattern_mean_pct:+.4f}, rastgele %{self.random_mean_pct:+.4f} "
            f"(%{self.random_p05_pct:+.4f} … %{self.random_p95_pct:+.4f}); "
            f"yüzdelik {self.percentile:.0%} — {verdict}."
        )


def compare_with_random_entries(
    outcomes: OutcomeTable,
    mask: np.ndarray,
    *,
    repeats: int = 2000,
    seed: int = 20260921,
    pool_mask: np.ndarray | None = None,
) -> RandomComparison:
    """Örüntünün ortalamasını, aynı büyüklükteki rastgele örneklerle kıyaslar.

    Bu bir permütasyon testidir: aynı olay havuzundan aynı sayıda mum
    rastgele seçilir, ortalaması alınır, binlerce kez tekrarlanır. Örüntünün
    ortalaması bu dağılımın neresinde? Sağ uçtaysa zamanlama bilgi taşıyor
    demektir; ortalardaysa örüntü rastgeleden ayırt edilemiyordur.
    """
    eligible = outcomes.eligible
    if pool_mask is not None:
        # Kıyas, örüntünün ölçüldüğü dönemle aynı dönemden yapılmalı; yoksa
        # farklı piyasa koşullarını karşılaştırmış oluruz.
        eligible = eligible & np.asarray(pool_mask, dtype=bool)
    selected = np.asarray(mask, dtype=bool) & eligible
    pool = outcomes.net_pct[eligible]
    pool = pool[np.isfinite(pool)]
    size = int(selected.sum())

    if size == 0 or pool.size < 2:
        return RandomComparison(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)

    observed = float(np.nanmean(outcomes.net_pct[selected]))
    generator = np.random.default_rng(seed)
    draws = generator.integers(0, pool.size, size=(repeats, size))
    means = pool[draws].mean(axis=1)
    low, high = np.percentile(means, [5, 95])

    return RandomComparison(
        pattern_mean_pct=observed,
        random_mean_pct=float(means.mean()),
        random_p05_pct=float(low),
        random_p95_pct=float(high),
        percentile=float((means < observed).mean()),
        repeats=repeats,
        sample_size=size,
    )


def random_entry_backtest(
    frame: pd.DataFrame,
    outcomes: OutcomeTable,
    *,
    signal_count: int,
    repeats: int = 200,
    seed: int = 20260921,
    start_equity: float = 100.0,
) -> list[BacktestResult]:
    """Rastgele mumlarda giriş yapan backtest'leri çalıştırır.

    Örüntü backtest'iyle aynı motoru, aynı hedef/stop kurallarını ve aynı
    "pozisyon açıkken yeni işlem yok" kısıtını kullanır. Tek fark girişlerin
    seçimi.
    """
    eligible = np.flatnonzero(outcomes.eligible)
    if eligible.size == 0 or signal_count <= 0:
        return []

    generator = np.random.default_rng(seed)
    count = min(signal_count, eligible.size)
    results = []
    for _ in range(repeats):
        chosen = generator.choice(eligible, size=count, replace=False)
        signals = np.zeros(len(frame), dtype=bool)
        signals[chosen] = True
        results.append(run(frame, outcomes, signals, start_equity=start_equity))
    return results


@dataclass(frozen=True)
class BuyAndHold:
    """Dönem başında alıp sonunda satmak."""

    gross_pct: float
    net_pct: float
    start_price: float
    end_price: float

    @property
    def summary_tr(self) -> str:
        return (
            f"Al-ve-tut: brüt %{self.gross_pct:+.2f}, "
            f"komisyon sonrası %{self.net_pct:+.2f}"
        )


def buy_and_hold(frame: pd.DataFrame, trip: RoundTrip) -> BuyAndHold:
    """Tek bir gidiş-dönüş komisyonu düşülmüş al-ve-tut getirisi."""
    if len(frame) < 2:
        return BuyAndHold(0.0, 0.0, 0.0, 0.0)
    start = float(frame["open"].iloc[0])
    end = float(frame["close"].iloc[-1])
    net = float(
        net_margin_pct_array(trip, np.array([start]), np.array([end]))[0]
    )
    return BuyAndHold(
        gross_pct=(end - start) / start * 100.0,
        net_pct=net,
        start_price=start,
        end_price=end,
    )


def random_entry_summary(results: list[BacktestResult]) -> dict[str, float]:
    """Rastgele backtest'lerin dağılım özeti."""
    if not results:
        return {}
    totals = np.array([r.total_return_pct for r in results], dtype=float)
    return {
        "ortalama": float(totals.mean()),
        "medyan": float(np.median(totals)),
        "p05": float(np.percentile(totals, 5)),
        "p95": float(np.percentile(totals, 95)),
        "en_iyi": float(totals.max()),
        "en_kotu": float(totals.min()),
    }
