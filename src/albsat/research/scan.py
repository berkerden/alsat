"""Örüntü taraması: özelliklerden istatistiksel olarak savunulabilir bulgulara.

Akış:

1. **Aday üretimi** — tek özellikler ve ikili kesişimleri. 56 özellik 1.596
   aday demektir; bu sayı çoklu test düzeltmesinin neden zorunlu olduğunu da
   gösterir.
2. **Ucuz eleme** — matris çarpımıyla her adayın olay sayısı ve ortalama
   getirisi tek seferde hesaplanır. Örnek sayısı eşiğin altında kalanlar ve
   eğitim döneminde zarar edenler burada düşer.
3. **Pahalı inceleme** — kalanlara bootstrap güven aralığı, rastgele giriş
   kıyası, çeyreklik kararlılık ve walk-forward uygulanır.
4. **Çoklu test düzeltmesi** — Benjamini–Hochberg, **denenen tüm adaylar**
   üzerinden. Eleme aşamasının kendisi de bir seçim olduğu için düzeltme
   1.596 üzerinden yapılır, incelenen 150 üzerinden değil.

**Keşif eğitim döneminde yapılır, sayı test döneminden okunur.** Veriyi
karıştırmak (shuffle) yok: bölmeler zaman sırasına göredir, çünkü geleceğin
bir kısmını eğitime sızdırmak bu işte en pahalı hatadır.

Tarama iki liste üretir:

* **Alınacaklar** — net beklenen değeri anlamlı biçimde pozitif örüntüler.
* **Kaçınılacaklar** — sonrasında fiyatın anlamlı biçimde düştüğü örüntüler.
  Spot'ta açığa satış yok; bu liste "alma" ve "elindekini sat" anlamına
  gelir (SPEC.md §4.4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from albsat.backtest.benchmarks import RandomComparison, compare_with_random_entries
from albsat.features import FeatureSet
from albsat.research.eventstudy import EXIT_TARGET, EventSummary, OutcomeTable, summarize
from albsat.research.stats import BootstrapResult, benjamini_hochberg, bootstrap_mean
from albsat.research.walkforward import (
    Split,
    StabilityRow,
    positive_period_share,
    stability_by_period,
    train_validation_test,
    walk_forward_splits,
)

#: SPEC.md §4.2: "Minimum örnek sayısı eşiği (varsayılan n ≥ 50)".
MIN_EVENTS = 50


@dataclass(frozen=True)
class ScanConfig:
    """Tarama ayarları."""

    min_events: int = MIN_EVENTS
    max_features: int = 2
    alpha: float = 0.10
    detailed_top: int = 150
    bootstrap_iterations: int = 2000
    random_repeats: int = 2000
    walk_forward_folds: int = 4
    seed: int = 20260921


@dataclass(frozen=True)
class SplitStats:
    """Bir zaman diliminde örüntünün performansı."""

    name: str
    events: int
    net_mean_pct: float
    hit_rate: float

    @property
    def positive(self) -> bool:
        return self.events > 0 and self.net_mean_pct > 0.0


@dataclass(frozen=True)
class PatternResult:
    """Tek bir örüntünün tüm bulguları."""

    features: tuple[str, ...]
    label_tr: str
    direction: str
    full: EventSummary
    splits: tuple[SplitStats, ...]
    bootstrap: BootstrapResult
    q_value: float
    accepted: bool
    random: RandomComparison
    stability: tuple[StabilityRow, ...]
    walk_forward_mean_pct: float
    walk_forward_positive_share: float
    warnings_tr: tuple[str, ...] = field(default_factory=tuple)

    @property
    def stable_share(self) -> float:
        return positive_period_share(list(self.stability))

    def split(self, name: str) -> SplitStats | None:
        for item in self.splits:
            if item.name == name:
                return item
        return None

    @property
    def trustworthy(self) -> bool:
        """Kabul edildi, uyarısı yok ve test döneminde de artıda."""
        test = self.split("test")
        return (
            self.accepted
            and not self.warnings_tr
            and test is not None
            and test.positive
        )


@dataclass(frozen=True)
class ScanResult:
    """Bir sembol + periyot + hedef penceresi için tarama sonucu."""

    symbol: str
    interval: str
    outcomes: OutcomeTable
    baseline: EventSummary
    candidates: int
    evaluated: int
    buy_patterns: tuple[PatternResult, ...]
    avoid_patterns: tuple[PatternResult, ...]
    config: ScanConfig

    @property
    def accepted_count(self) -> int:
        return sum(1 for p in self.buy_patterns if p.accepted)


def _label(features: tuple[str, ...], feature_set: FeatureSet) -> str:
    return " + ".join(feature_set.describe(name) for name in features)


def _candidate_matrix(
    feature_set: FeatureSet, usable: np.ndarray
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Kullanılabilir satırlarla sınırlanmış özellik matrisi."""
    names = tuple(feature_set.frame.columns)
    matrix = feature_set.frame.to_numpy(dtype=bool)[usable]
    return matrix, names


def _screen(
    matrix: np.ndarray,
    names: tuple[str, ...],
    values: np.ndarray,
    *,
    min_events: int,
    max_features: int,
    positive: bool,
) -> list[tuple[tuple[str, ...], int, float]]:
    """Tüm adayların olay sayısı ve ortalamasını matris çarpımıyla hesaplar.

    Tek tek döngü kurmak 1.596 adayda bile dakikalar sürerdi; iki matris
    çarpımı saniyenin altında bitiriyor.
    """
    float_matrix = matrix.astype(np.float32)
    counts_single = float_matrix.sum(axis=0)
    sums_single = (float_matrix * values[:, None].astype(np.float32)).sum(axis=0)

    candidates: list[tuple[tuple[str, ...], int, float]] = []
    for index, name in enumerate(names):
        count = int(counts_single[index])
        if count >= min_events:
            candidates.append(((name,), count, float(sums_single[index] / count)))

    if max_features >= 2:
        counts_pair = float_matrix.T @ float_matrix
        weighted = float_matrix * values[:, None].astype(np.float32)
        sums_pair = weighted.T @ float_matrix
        size = len(names)
        for first in range(size):
            for second in range(first + 1, size):
                count = int(counts_pair[first, second])
                if count < min_events:
                    continue
                # Biri diğerini kapsıyorsa ikili yeni bilgi taşımaz.
                if count == int(counts_single[first]) or count == int(counts_single[second]):
                    continue
                candidates.append(
                    (
                        (names[first], names[second]),
                        count,
                        float(sums_pair[first, second] / count),
                    )
                )

    keep = [item for item in candidates if (item[2] > 0) == positive and item[2] != 0]
    keep.sort(key=lambda item: item[2], reverse=positive)
    return keep


def _mask_for(features: tuple[str, ...], feature_set: FeatureSet) -> np.ndarray:
    mask = np.ones(len(feature_set.frame), dtype=bool)
    for name in features:
        mask &= feature_set.frame[name].to_numpy(dtype=bool)
    return mask


def _split_stats(
    name: str,
    mask: np.ndarray,
    split: Split,
    outcomes: OutcomeTable,
    values: np.ndarray,
) -> SplitStats:
    selected = mask & outcomes.eligible & split.mask(len(mask))
    count = int(selected.sum())
    if count == 0:
        return SplitStats(name, 0, 0.0, 0.0)
    return SplitStats(
        name=name,
        events=count,
        net_mean_pct=float(np.nanmean(values[selected])),
        hit_rate=float((outcomes.exit_reason[selected] == EXIT_TARGET).mean()),
    )


def _warnings(
    result_splits: tuple[SplitStats, ...],
    summary: EventSummary,
    bootstrap: BootstrapResult,
    random: RandomComparison,
    stability: list[StabilityRow],
    walk_forward_positive: float,
    *,
    min_events: int,
    direction: str,
) -> tuple[str, ...]:
    """SPEC.md §4.2: aşırı uyum riskini açıkça yaz."""
    notes: list[str] = []
    if summary.independent_events < min_events:
        notes.append(
            f"Bu örüntü az bağımsız örneğe dayanıyor "
            f"(üst üste binmeyen olay: {summary.independent_events})."
        )
    test = next((s for s in result_splits if s.name == "test"), None)
    train = next((s for s in result_splits if s.name == "eğitim"), None)
    if test is not None and train is not None and train.events and test.events:
        wrong_way = test.net_mean_pct <= 0 if direction == "al" else test.net_mean_pct >= 0
        if wrong_way:
            notes.append("Test döneminde performans tersine döndü.")
    if not bootstrap.significant:
        notes.append("Güven aralığı sıfırı içeriyor; sonuç gürültüden ayrışmıyor.")
    if direction == "al" and not random.beats_random:
        notes.append("Rastgele girişten ayırt edilemiyor.")
    if stability and positive_period_share(stability) < 0.5:
        notes.append("Dönemlerin yarısından azında doğru yönde.")
    if walk_forward_positive < 0.5:
        notes.append("Walk-forward katmanlarının yarısından azında doğru yönde.")
    return tuple(notes)


def _evaluate(
    features: tuple[str, ...],
    *,
    feature_set: FeatureSet,
    outcomes: OutcomeTable,
    values: np.ndarray,
    open_time: np.ndarray,
    splits: tuple[Split, Split, Split],
    folds: list[tuple[Split, Split]],
    config: ScanConfig,
    direction: str,
) -> PatternResult:
    mask = _mask_for(features, feature_set)
    selected = mask & outcomes.eligible
    summary = summarize(outcomes, mask)

    # İstatistiksel test üst üste binmeyen olaylarla yapılır: aynı fiyat
    # hareketini birden çok kez saymak güven aralığını sahte biçimde daraltır.
    from albsat.research.eventstudy import independent_indices

    independent = independent_indices(selected, outcomes.config.horizon)
    sample = values[independent] if independent.size else np.zeros(0)
    if direction == "kaçın":
        sample = -sample
    bootstrap = bootstrap_mean(
        sample, iterations=config.bootstrap_iterations, seed=config.seed
    )

    split_stats = tuple(
        _split_stats(split.name, mask, split, outcomes, values) for split in splits
    )

    random = compare_with_random_entries(
        outcomes, mask, repeats=config.random_repeats, seed=config.seed
    )

    stability = stability_by_period(
        open_time, values, (outcomes.exit_reason == EXIT_TARGET), selected
    )

    out_of_sample = []
    for _, test_split in folds:
        stats = _split_stats("katman", mask, test_split, outcomes, values)
        if stats.events:
            out_of_sample.append(stats.net_mean_pct)
    walk_mean = float(np.mean(out_of_sample)) if out_of_sample else 0.0
    if out_of_sample:
        correct = [v > 0 for v in out_of_sample] if direction == "al" else [
            v < 0 for v in out_of_sample
        ]
        walk_share = float(np.mean(correct))
    else:
        walk_share = 0.0

    notes = _warnings(
        split_stats,
        summary,
        bootstrap,
        random,
        stability,
        walk_share,
        min_events=config.min_events,
        direction=direction,
    )

    return PatternResult(
        features=features,
        label_tr=_label(features, feature_set),
        direction=direction,
        full=summary,
        splits=split_stats,
        bootstrap=bootstrap,
        q_value=1.0,
        accepted=False,
        random=random,
        stability=tuple(stability),
        walk_forward_mean_pct=walk_mean,
        walk_forward_positive_share=walk_share,
        warnings_tr=notes,
    )


def _apply_correction(
    results: list[PatternResult], *, total_tests: int, alpha: float
) -> tuple[PatternResult, ...]:
    if not results:
        return ()
    p_values = np.array([item.bootstrap.p_value for item in results], dtype=float)
    accepted, q_values = benjamini_hochberg(p_values, alpha, total_tests=total_tests)
    corrected = []
    for item, is_accepted, q_value in zip(results, accepted, q_values, strict=True):
        corrected.append(
            PatternResult(
                features=item.features,
                label_tr=item.label_tr,
                direction=item.direction,
                full=item.full,
                splits=item.splits,
                bootstrap=item.bootstrap,
                q_value=float(q_value),
                accepted=bool(is_accepted),
                random=item.random,
                stability=item.stability,
                walk_forward_mean_pct=item.walk_forward_mean_pct,
                walk_forward_positive_share=item.walk_forward_positive_share,
                warnings_tr=item.warnings_tr,
            )
        )
    corrected.sort(key=lambda r: (not r.accepted, r.q_value))
    return tuple(corrected)


def scan(
    frame: pd.DataFrame,
    feature_set: FeatureSet,
    outcomes: OutcomeTable,
    *,
    symbol: str,
    interval: str,
    config: ScanConfig | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> ScanResult:
    """Tek bir sembol + periyot + hedef penceresi için tam tarama."""
    config = config or ScanConfig()
    count = int(len(frame))
    open_time = frame["open_time"].to_numpy()

    splits = train_validation_test(count)
    folds = walk_forward_splits(count, folds=config.walk_forward_folds)
    baseline = summarize(outcomes, np.ones(count, dtype=bool))

    # Eleme yalnızca EĞİTİM döneminde yapılır; doğrulama ve test dokunulmadan
    # kalır, böylece sonlardaki sayılar gerçekten "görülmemiş veri"dir.
    train = splits[0].mask(count)
    usable = outcomes.eligible & train
    net = outcomes.net_pct
    forward = outcomes.forward_pct

    if usable.sum() < config.min_events:
        return ScanResult(symbol, interval, outcomes, baseline, 0, 0, (), (), config)

    matrix, names = _candidate_matrix(feature_set, usable)

    buy_candidates = _screen(
        matrix, names, np.nan_to_num(net[usable]),
        min_events=config.min_events, max_features=config.max_features, positive=True,
    )
    avoid_candidates = _screen(
        matrix, names, np.nan_to_num(forward[usable]),
        min_events=config.min_events, max_features=config.max_features, positive=False,
    )
    total_candidates = len(buy_candidates) + len(avoid_candidates)

    def evaluate_list(
        candidates: list[tuple[tuple[str, ...], int, float]],
        values: np.ndarray,
        direction: str,
    ) -> list[PatternResult]:
        chosen = candidates[: config.detailed_top]
        results: list[PatternResult] = []
        for position, (features, _, _) in enumerate(chosen, start=1):
            results.append(
                _evaluate(
                    features,
                    feature_set=feature_set,
                    outcomes=outcomes,
                    values=values,
                    open_time=open_time,
                    splits=splits,
                    folds=folds,
                    config=config,
                    direction=direction,
                )
            )
            if on_progress is not None:
                on_progress(direction, position, len(chosen))
        return results

    buy_results = evaluate_list(buy_candidates, net, "al")
    avoid_results = evaluate_list(avoid_candidates, forward, "kaçın")

    return ScanResult(
        symbol=symbol,
        interval=interval,
        outcomes=outcomes,
        baseline=baseline,
        candidates=total_candidates,
        evaluated=len(buy_results) + len(avoid_results),
        buy_patterns=_apply_correction(
            buy_results, total_tests=total_candidates, alpha=config.alpha
        ),
        avoid_patterns=_apply_correction(
            avoid_results, total_tests=total_candidates, alpha=config.alpha
        ),
        config=config,
    )
