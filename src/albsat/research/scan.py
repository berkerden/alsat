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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from albsat.backtest.benchmarks import RandomComparison, compare_with_random_entries
from albsat.features import FeatureSet
from albsat.research.eventstudy import (
    EXIT_TARGET,
    EventSummary,
    OutcomeTable,
    independent_indices,
    summarize,
)
from albsat.research.stats import (
    BootstrapResult,
    benjamini_hochberg,
    bootstrap_mean,
    p_value_floor,
    required_iterations,
)
from albsat.research.walkforward import (
    Split,
    StabilityRow,
    period_share_in_direction,
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
    #: Kabul kararını veren örneklem: ayrılmış dönemdeki üst üste binmeyen olaylar.
    inference_events: int = 0

    @property
    def stable_share(self) -> float:
        """Dönemlerin kaçta kaçı örüntünün beklenen yönünde?"""
        return period_share_in_direction(list(self.stability), direction=self.direction)

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
    #: Düzeltmenin yapıldığı aile büyüklüğü. 0 ise yalnızca bu bölüm sayıldı;
    #: koşu genelinde düzeltme uygulandığında tüm bölümlerin toplamı yazılır.
    family_tests: int = 0

    @property
    def effective_tests(self) -> int:
        """Kabul kararının kaç deneme üzerinden verildiği."""
        return self.family_tests or self.candidates

    @property
    def accepted_count(self) -> int:
        return sum(1 for p in self.buy_patterns if p.accepted)

    @property
    def best_raw_p_value(self) -> float:
        """İncelenen örüntüler içindeki en küçük düzeltilmemiş p-değeri.

        "Bulunamadı" sonucunu okurken en önemli sayı budur: en iyi aday
        eşiğin hemen yanında mıydı, yoksa çok mu uzaktaydı?
        """
        values = [
            item.bootstrap.p_value for item in self.buy_patterns + self.avoid_patterns
        ]
        return min(values) if values else 1.0

    @property
    def acceptance_p_threshold(self) -> float:
        """Tek bir örüntünün kabul edilmesi için gereken en küçük p-değeri."""
        if self.effective_tests < 1:
            return 0.0
        return self.config.alpha / self.effective_tests


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
    inference_events: int,
    min_events: int,
    direction: str,
) -> tuple[str, ...]:
    """SPEC.md §4.2: aşırı uyum riskini açıkça yaz."""
    notes: list[str] = []
    if inference_events < min_events:
        notes.append(
            f"Kabul kararı az bağımsız örneğe dayanıyor (ayrılmış dönemde "
            f"üst üste binmeyen olay: {inference_events}, "
            f"tüm seride: {summary.independent_events})."
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
    if stability and period_share_in_direction(stability, direction=direction) < 0.5:
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
    holdout: np.ndarray,
    config: ScanConfig,
    direction: str,
) -> PatternResult:
    mask = _mask_for(features, feature_set)
    selected = mask & outcomes.eligible
    summary = summarize(outcomes, mask)

    # Kabul kararını veren p-değeri **yalnızca ayrılmış dönemden** hesaplanır.
    # Adaylar eğitim dönemine bakılarak seçildi; aynı veriyle sınamak seçimin
    # kendisini kanıt sayardı. Tüm seriden hesaplanan sayılar raporda bağlam
    # olarak duruyor, kararı vermiyor.
    #
    # Test üst üste binmeyen olaylarla yapılır: aynı fiyat hareketini birden
    # çok kez saymak güven aralığını sahte biçimde daraltır.
    sample = _inference_sample(
        features,
        feature_set=feature_set,
        outcomes=outcomes,
        values=values,
        direction=direction,
        holdout=holdout,
    )
    bootstrap = bootstrap_mean(
        sample, iterations=config.bootstrap_iterations, seed=config.seed
    )

    split_stats = tuple(
        _split_stats(split.name, mask, split, outcomes, values) for split in splits
    )

    random = compare_with_random_entries(
        outcomes, mask, repeats=config.random_repeats, seed=config.seed,
        pool_mask=holdout,
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
        inference_events=int(sample.size),
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
        inference_events=int(sample.size),
    )


def _inference_sample(
    features: tuple[str, ...],
    *,
    feature_set: FeatureSet,
    outcomes: OutcomeTable,
    values: np.ndarray,
    direction: str,
    holdout: np.ndarray,
) -> np.ndarray:
    """Bir örüntünün istatistiksel teste giren örneklemi.

    Yalnızca **ayrılmış dönem** (doğrulama + test) ve yalnızca üst üste
    binmeyen olaylar. Keşif eğitim döneminde yapıldığı için sınama oraya
    dokunmaz.
    """
    selected = _mask_for(features, feature_set) & outcomes.eligible & holdout
    independent = independent_indices(selected, outcomes.config.horizon)
    if independent.size == 0:
        return np.zeros(0)
    sample = np.asarray(values[independent], dtype=float)
    return -sample if direction == "kaçın" else sample


def _refine_floor_pvalues(
    results: list[PatternResult],
    *,
    feature_set: FeatureSet,
    outcomes: OutcomeTable,
    values: np.ndarray,
    direction: str,
    holdout: np.ndarray,
    total_tests: int,
    config: ScanConfig,
) -> list[PatternResult]:
    """Tabana oturan p-değerlerini daha yüksek çözünürlükle yeniden hesaplar.

    Ucuz turda p-değeri ``1/(yineleme+1)`` tabanına dayanmış örüntüler, o
    tabanın altında gerçekte ne kadar küçük olduklarını söyleyemez. Düzeltme
    eşiği tabanın altındaysa bu örüntüler hak ettikleri halde reddedilir.
    Burada yalnızca o örüntüler, eşiği çözebilecek kadar yinelemeyle yeniden
    ölçülüyor.

    Normal durumda hiçbir örüntü tabana oturmaz ve bu tur hiç çalışmaz;
    maliyeti yalnızca gerçekten güçlü bir bulgu varken ödenir.
    """
    needed = required_iterations(total_tests, config.alpha)
    if needed <= config.bootstrap_iterations:
        return results

    floor = p_value_floor(config.bootstrap_iterations)
    refined: list[PatternResult] = []
    for item in results:
        if item.bootstrap.p_value > floor:
            refined.append(item)
            continue
        sample = _inference_sample(
            item.features,
            feature_set=feature_set,
            outcomes=outcomes,
            values=values,
            direction=direction,
            holdout=holdout,
        )
        refined.append(
            replace(
                item,
                bootstrap=bootstrap_mean(sample, iterations=needed, seed=config.seed),
            )
        )
    return refined


def _apply_correction(
    results: list[PatternResult], *, total_tests: int, alpha: float
) -> tuple[PatternResult, ...]:
    if not results:
        return ()
    p_values = np.array([item.bootstrap.p_value for item in results], dtype=float)
    accepted, q_values = benjamini_hochberg(p_values, alpha, total_tests=total_tests)
    corrected = []
    for item, is_accepted, q_value in zip(results, accepted, q_values, strict=True):
        # replace(): alan alan kopyalamak, sonradan eklenen bir alanı sessizce
        # varsayılana düşürür. Böyle bir hata bir kez yaşandı.
        corrected.append(
            replace(item, q_value=float(q_value), accepted=bool(is_accepted))
        )
    corrected.sort(key=lambda r: (not r.accepted, r.q_value))
    return tuple(corrected)


def apply_global_correction(
    results: Sequence[ScanResult], *, alpha: float | None = None
) -> list[ScanResult]:
    """Koşunun tamamını **tek bir aile** sayarak düzeltmeyi yeniden uygular.

    Bir koşuda 12 bölüm varsa (2 sembol × 2 periyot × 3 pencere) ve her bölüm
    kendi içinde %10 yanlış buluş payıyla düzeltilirse, ortada hiçbir şey
    yokken bile ortalama ``12 × 0,10 ≈ 1,2`` bölümde bir "buluş" çıkar. Bölüm
    içi düzeltme bunu göremez, çünkü kaç bölüm çalıştırıldığını bilmez. Bu,
    teoride değil pratikte oldu: 22 Eylül 2026 koşusunda tam olarak bir
    örüntü kabul edildi — şansın üreteceği sayının kendisi.

    Bu yüzden kabul kararı, tüm bölümlerin p-değerleri havuzlanarak ve deneme
    sayısı tüm bölümlerin adayları toplanarak veriliyor. Tek bölümlük bir
    koşuda sonuç değişmez.
    """
    items = list(results)
    if not items:
        return []

    alpha = items[0].config.alpha if alpha is None else alpha
    total = sum(item.candidates for item in items)

    flat: list[PatternResult] = []
    for item in items:
        flat.extend(item.buy_patterns)
        flat.extend(item.avoid_patterns)
    if not flat:
        return [replace(item, family_tests=total) for item in items]

    p_values = np.array([pattern.bootstrap.p_value for pattern in flat], dtype=float)
    accepted, q_values = benjamini_hochberg(p_values, alpha, total_tests=total)

    decided = {
        id(pattern): (bool(is_accepted), float(q_value))
        for pattern, is_accepted, q_value in zip(flat, accepted, q_values, strict=True)
    }

    def redo(patterns: tuple[PatternResult, ...]) -> tuple[PatternResult, ...]:
        out = [
            replace(pattern, accepted=decided[id(pattern)][0],
                    q_value=decided[id(pattern)][1])
            for pattern in patterns
        ]
        out.sort(key=lambda item: (not item.accepted, item.q_value))
        return tuple(out)

    return [
        replace(
            item,
            buy_patterns=redo(item.buy_patterns),
            avoid_patterns=redo(item.avoid_patterns),
            family_tests=total,
        )
        for item in items
    ]


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
    # Keşif eğitim döneminde, sınama ayrılmış dönemde. İkisi aynı veriyi
    # kullanırsa seçimin kendisi kanıt sayılır ve p-değeri anlamını yitirir.
    holdout = ~train
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
                    holdout=holdout,
                    config=config,
                    direction=direction,
                )
            )
            if on_progress is not None:
                on_progress(direction, position, len(chosen))
        return results

    buy_results = evaluate_list(buy_candidates, net, "al")
    avoid_results = evaluate_list(avoid_candidates, forward, "kaçın")

    # Çözünürlük turu: p-değeri tabana dayanmış örüntüler varsa onları,
    # düzeltme eşiğini çözebilecek yinelemeyle yeniden ölç.
    buy_results = _refine_floor_pvalues(
        buy_results, feature_set=feature_set, outcomes=outcomes, values=net,
        direction="al", total_tests=total_candidates, config=config, holdout=holdout,
    )
    avoid_results = _refine_floor_pvalues(
        avoid_results, feature_set=feature_set, outcomes=outcomes, values=forward,
        direction="kaçın", total_tests=total_candidates, config=config, holdout=holdout,
    )

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
