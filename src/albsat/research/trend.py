"""Günlük trend testi (docs/TREND-ONKAYIT.md).

Soru: günlük grafikte, önceden seçilmiş klasik trend takibi kuralları
"yalnızca eğilim yukarıyken elde tut, değilse nakitte bekle" diyerek
**al-ve-tut'un büyük düşüşlerinin bir kısmından kaçıyor mu**, ve bunu
şansla açıklanamayacak biçimde mi yapıyor?

Faz 2 ve ikinci turdan üç fark:

* **Aday uzayı yok.** Beş kural ve parametreleri kaynaklarındaki haliyle,
  veriye bakılmadan seçildi. Kural kodda sabittir; buradan başka bir kural
  ya da parametre denemek yeni bir sınama olur.
* **Soru işlem başına kâr değil, dönemin tamamı.** Bir kural yılda birkaç
  kez alıp satar; ölçü, bütün dönemin risk başına getirisi (Sharpe) ve en
  büyük düşüşüdür.
* **Kıyas al-ve-tut'la.** Sınama "kuralın Sharpe'ı al-ve-tut'unkinden
  yüksek mi, ve bu üstünlük birkaç şanslı dönemden mi geliyor" sorusudur
  (:func:`difference_tests`, eşli durağan blok bootstrap).

  İlk tasarımda iki başka sıfır hipotezi denendi ve yapay veride elendi
  (docs/TREND-ONKAYIT.md §6): aynı gün sayısı piyasada kalan rastgele
  zamanlama, eğilim yokken bile kuralları sistematik olarak cezalandırıyordu;
  getirilerin sırasını karıştırmak, düşüşten sonra oynaklığın arttığı
  eğilimsiz veride iki coinden en az birinde "Geçti" çıkma oranını izin
  verilen %10'un üstüne, %28'e çıkarıyordu.

**Nedensellik:** ``t`` günü kapanışında verilen karar yalnızca ``0..t``
kapanışlarına bakar ve ``t+1`` gününün getirisini alır. Kurallar yalnızca
kapanış fiyatını kullanır; ``tests/test_trend.py`` geleceği bozarak bunu
mekanik olarak doğrular.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from albsat.research.stats import benjamini_hochberg, max_drawdown_pct, sharpe_ratio

#: Kripto piyasası yılın her günü açık; yıllıklandırma 365 günle yapılır.
DAYS_PER_YEAR = 365

#: Ön kayıtla sabitlenen sınama ayarları. Değiştirmek yeni bir sınamadır.
RESAMPLES = 10_000
BLOCK_DAYS = 20
SEED = 20260927
FDR_ALPHA = 0.10

#: Bootstrap örnekleri bu büyüklükte parçalar halinde üretilir (bellek).
CHUNK = 1_000


# --- kurallar --------------------------------------------------------------------
#
# Her kural bir kapanış matrisi alır (satır: bir fiyat geçmişi, sütun: gün) ve
# aynı biçimde "elde tut" matrisi döndürür. Gerçek geçmiş tek satırlık bir
# matristir; testler ve yapay veri ölçümleri birçok geçmişi aynı fonksiyonla
# birlikte sınar.


def _rolling_mean(close: np.ndarray, window: int) -> np.ndarray:
    # Toplamlar ilk kapanışa bölünmüş fiyatlarla alınır: büyük fiyatların
    # birikimli toplamında kayan nokta hatası büyümesin.
    out = np.full(close.shape, np.nan)
    if close.shape[1] < window:
        return out
    scaled = close / close[:, :1]
    total = np.cumsum(scaled, axis=1)
    out[:, window - 1] = total[:, window - 1]
    out[:, window:] = total[:, window:] - total[:, :-window]
    return np.asarray(out / window * close[:, :1], dtype=float)


def _previous_extreme(close: np.ndarray, window: int, *, highest: bool) -> np.ndarray:
    """``t`` gününden **önceki** ``window`` günün en yüksek/düşük kapanışı.

    Pencere bu günü içermez; içerseydi her yeni zirve kendi kendini kırmış
    sayılırdı.
    """
    out = np.full(close.shape, np.nan)
    if close.shape[1] <= window:
        return out
    view = sliding_window_view(close[:, :-1], window, axis=1)
    out[:, window:] = view.max(axis=-1) if highest else view.min(axis=-1)
    return out


def _above_sma(close: np.ndarray) -> np.ndarray:
    return np.asarray(close > _rolling_mean(close, 200), dtype=bool)


def _golden_cross(close: np.ndarray) -> np.ndarray:
    return np.asarray(_rolling_mean(close, 50) > _rolling_mean(close, 200), dtype=bool)


def _momentum_365(close: np.ndarray) -> np.ndarray:
    out = np.zeros(close.shape, dtype=bool)
    out[:, 365:] = close[:, 365:] > close[:, :-365]
    return out


def _donchian(entry: int, exit_: int) -> Callable[[np.ndarray], np.ndarray]:
    def positions(close: np.ndarray) -> np.ndarray:
        upper = _previous_extreme(close, entry, highest=True)
        lower = _previous_extreme(close, exit_, highest=False)
        out = np.zeros(close.shape, dtype=bool)
        holding = np.zeros(close.shape[0], dtype=bool)
        # NaN ile her karşılaştırma yanlıştır: ısınmada alış olmaz.
        for day in range(close.shape[1]):
            holding = np.where(holding, ~(close[:, day] < lower[:, day]),
                               close[:, day] > upper[:, day])
            out[:, day] = holding
        return out

    return positions


@dataclass(frozen=True)
class TrendRule:
    """Önceden seçilmiş tek bir kural."""

    key: str
    name_tr: str
    rule_tr: str
    source_tr: str
    #: Kuralın ilk kez karar verebildiği gün (0'dan sayılır).
    warmup: int
    compute: Callable[[np.ndarray], np.ndarray] = field(repr=False, compare=False)

    def matrix(self, close: np.ndarray) -> np.ndarray:
        """Kapanış matrisinden karar matrisi; ısınma bitmeden karar yoktur (nakit)."""
        out = np.asarray(self.compute(np.asarray(close, dtype=float)), dtype=bool).copy()
        out[:, : self.warmup] = False
        return out

    def positions(self, frame: pd.DataFrame) -> np.ndarray:
        """``t`` kapanışındaki karar: ``True`` elde tut, ``False`` nakitte bekle."""
        close = frame["close"].to_numpy(dtype=float)[None, :]
        return np.asarray(self.matrix(close)[0], dtype=bool)


#: Ön kayıttaki beş kural (docs/TREND-ONKAYIT.md §3). Sıra raporun sırasıdır.
RULES: tuple[TrendRule, ...] = (
    TrendRule(
        key="sma200",
        name_tr="200 günlük ortalama",
        rule_tr="Kapanış 200 günlük basit ortalamanın üstündeyse elde tut, altındaysa sat.",
        source_tr="En bilinen trend filtresi; Faber (2007) aylık karşılığını kullanır.",
        warmup=199,
        compute=_above_sma,
    ),
    TrendRule(
        key="kesisim_50_200",
        name_tr="50/200 kesişimi",
        rule_tr="50 günlük ortalama 200 günlüğün üstündeyse elde tut (\"altın kesişim\"), "
        "altına inince sat.",
        source_tr="Klasik hareketli ortalama kesişimi.",
        warmup=199,
        compute=_golden_cross,
    ),
    TrendRule(
        key="kirilim_55_20",
        name_tr="55/20 gün kırılımı",
        rule_tr="Kapanış önceki 55 günün en yüksek kapanışını aşınca al, önceki 20 günün "
        "en düşük kapanışının altına inince sat.",
        source_tr="Donchian kanalı; \"Kaplumbağalar\"ın 2. sistemi (kapanışla).",
        warmup=55,
        compute=_donchian(55, 20),
    ),
    TrendRule(
        key="kirilim_20_10",
        name_tr="20/10 gün kırılımı",
        rule_tr="Kapanış önceki 20 günün en yüksek kapanışını aşınca al, önceki 10 günün "
        "en düşük kapanışının altına inince sat.",
        source_tr="Donchian kanalı; \"Kaplumbağalar\"ın 1. sistemi (kapanışla, filtresiz).",
        warmup=20,
        compute=_donchian(20, 10),
    ),
    TrendRule(
        key="momentum_365",
        name_tr="12 aylık momentum",
        rule_tr="Kapanış 365 gün önceki kapanışın üstündeyse elde tut, altındaysa sat.",
        source_tr="Zaman serisi momentumu; Moskowitz, Ooi ve Pedersen (2012).",
        warmup=365,
        compute=_momentum_365,
    ),
)

#: Bütün kuralların karar verebildiği ilk gün; değerlendirme buradan başlar.
EVALUATION_START = max(rule.warmup for rule in RULES)


# --- getiri, maliyet ve ölçüler ----------------------------------------------------


def net_growth(positions: np.ndarray, returns: np.ndarray, cost: float) -> np.ndarray:
    """Maliyet sonrası günlük büyüme çarpanları; son eksen gündür.

    ``positions[..., j]`` ``j`` günü başındaki (önceki kapanıştaki) karardır
    ve ``returns[..., j]`` getirisini alır. Dönem nakitle başlar; pozisyon
    her değiştiğinde tek yön maliyeti ``cost`` (oran) ödenir. Dönem sonunda
    pozisyon açıksa satılmış sayılır, böylece açık kalan kâr maliyetsiz
    görünmez.
    """
    held = np.asarray(positions, dtype=float)
    previous = np.concatenate((np.zeros(held.shape[:-1] + (1,)), held[..., :-1]), axis=-1)
    growth = (1.0 - cost * np.abs(held - previous)) * (1.0 + held * returns)
    growth[..., -1] *= 1.0 - cost * held[..., -1]
    return np.asarray(growth, dtype=float)


def daily_net_returns(positions: np.ndarray, returns: np.ndarray, cost: float) -> np.ndarray:
    return np.asarray(net_growth(positions, returns, cost) - 1.0, dtype=float)


def equity_curve(net: np.ndarray) -> np.ndarray:
    """Başlangıcı 1 olan sermaye eğrisi (ilk değer dönem başı)."""
    return np.concatenate(([1.0], np.cumprod(1.0 + net)))


@dataclass(frozen=True)
class Performance:
    total_return_pct: float
    annual_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    exposure_pct: float
    entries: int
    days: int

    @property
    def entries_per_year(self) -> float:
        return self.entries * DAYS_PER_YEAR / self.days if self.days else 0.0


def measure(positions: np.ndarray, returns: np.ndarray, cost: float) -> Performance:
    held = np.asarray(positions, dtype=bool)
    net = daily_net_returns(held, returns, cost)
    equity = equity_curve(net)
    days = int(held.size)
    final = float(equity[-1])
    annual = (final ** (DAYS_PER_YEAR / days) - 1.0) * 100.0 if days and final > 0 else -100.0
    previous = np.concatenate(([False], held[:-1]))
    return Performance(
        total_return_pct=(final - 1.0) * 100.0,
        annual_return_pct=annual,
        max_drawdown_pct=max_drawdown_pct(equity),
        sharpe=sharpe_ratio(net, periods_per_year=DAYS_PER_YEAR),
        exposure_pct=float(held.mean() * 100.0) if days else 0.0,
        entries=int(np.count_nonzero(held & ~previous)),
        days=days,
    )


# --- blok bootstrap: al-ve-tut'a göre Sharpe farkı ---------------------------------


def stationary_indices(
    length: int, count: int, block: float, generator: np.random.Generator
) -> np.ndarray:
    """Politis–Romano durağan bootstrap'ının gün indeksleri, ``(count, length)``.

    Her gün ``1/block`` olasılıkla rastgele bir günden yeni bir parça başlar,
    yoksa önceki günün ardından devam edilir (sona gelince başa sarar).
    Parçalar ortalama ``block`` gün uzunluğundadır; böylece oynaklığın
    kümelenmesi gibi kısa süreli bağımlılıklar parça içinde korunur.
    """
    starts = generator.integers(0, length, size=(count, length))
    fresh = generator.random((count, length)) < 1.0 / block
    out = np.empty((count, length), dtype=np.int64)
    out[:, 0] = starts[:, 0]
    for day in range(1, length):
        out[:, day] = np.where(fresh[:, day], starts[:, day], (out[:, day - 1] + 1) % length)
    return out


@dataclass(frozen=True)
class DifferenceTest:
    """Kuralın Sharpe'ı eksi al-ve-tut'un Sharpe'ı ve belirsizliği."""

    difference: float
    low: float
    high: float
    p_value: float
    resamples: int


def difference_tests(
    rule_returns: Sequence[np.ndarray],
    benchmark_returns: np.ndarray,
    *,
    resamples: int,
    block: float,
    seed: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[DifferenceTest]:
    """Her kural için "Sharpe'ı al-ve-tut'tan yüksek mi" sınaması (tek yönlü).

    Kural ve al-ve-tut aynı günlerden yeniden örneklenir (eşli), böylece
    ikisini birlikte etkileyen büyük günler farka iki kez sayılmaz. Sınama
    ortalanmış bootstrap'tır: ``p = (1 + (fark* − fark) ≥ fark) / (1 + deneme)``.
    Aralık, farkın %5–%95 yüzdelikleridir.
    """
    benchmark = np.asarray(benchmark_returns, dtype=float)
    rules = [np.asarray(item, dtype=float) for item in rule_returns]
    observed = [
        sharpe_rows_from_net(item) - sharpe_rows_from_net(benchmark) for item in rules
    ]
    generator = np.random.default_rng(seed)
    collected: list[list[np.ndarray]] = [[] for _ in rules]
    done = 0
    while done < resamples:
        size = min(CHUNK, resamples - done)
        index = stationary_indices(benchmark.size, size, block, generator)
        base = sharpe_rows_from_net(benchmark[index])
        for position, item in enumerate(rules):
            collected[position].append(sharpe_rows_from_net(item[index]) - base)
        done += size
        if on_progress is not None:
            on_progress(done, resamples)

    tests: list[DifferenceTest] = []
    for position, item in enumerate(collected):
        values = np.concatenate(item)
        estimate = float(observed[position])
        low, high = np.percentile(values, [5, 95])
        reached = int(np.count_nonzero(values - estimate >= estimate))
        tests.append(DifferenceTest(estimate, float(low), float(high),
                                    (reached + 1) / (resamples + 1), resamples))
    return tests


def sharpe_rows_from_net(net: np.ndarray) -> np.ndarray:
    """Net günlük getirilerin (son eksen) yıllık Sharpe'ı."""
    values = np.asarray(net, dtype=float)
    deviation = values.std(axis=-1, ddof=1)
    safe = np.where(deviation > 0, deviation, 1.0)
    ratio = np.where(deviation > 0, values.mean(axis=-1) / safe, 0.0)
    return np.asarray(ratio * math.sqrt(DAYS_PER_YEAR), dtype=float)


# --- büyük düşüşler ----------------------------------------------------------------


@dataclass(frozen=True)
class Drawdown:
    """Al-ve-tut eğrisinde bir tepe-dip düşüşü (sermaye eğrisi indeksleri)."""

    peak: int
    trough: int
    depth_pct: float
    #: Tepe ve dip günlerinin mum açılış zamanı (ms); tarih yazmak için.
    peak_time: int = 0
    trough_time: int = 0


def largest_drawdowns(equity: np.ndarray, count: int = 3) -> list[Drawdown]:
    """Üst üste binmeyen en derin ``count`` düşüş, derinlik sırasıyla.

    Bir düşüş yeni bir tepede başlar ve eğri o tepeyi geçince (ya da dönem
    bitince) biter; dibi bu aralığın en düşük noktasıdır.
    """
    values = np.asarray(equity, dtype=float)
    episodes: list[Drawdown] = []
    peak = trough = 0
    for index in range(1, values.size):
        if values[index] >= values[peak]:
            if trough != peak:
                episodes.append(Drawdown(peak, trough, (values[trough] / values[peak] - 1) * 100))
            peak = trough = index
        elif values[index] < values[trough]:
            trough = index
    if trough != peak:
        episodes.append(Drawdown(peak, trough, (values[trough] / values[peak] - 1) * 100))
    episodes.sort(key=lambda item: item.depth_pct)
    return episodes[:count]


# --- bir coinin bütün sınaması -----------------------------------------------------


@dataclass(frozen=True)
class RuleResult:
    rule: TrendRule
    performance: Performance
    delayed: Performance
    test: DifferenceTest
    #: Al-ve-tut'un en derin düşüşlerinde kuralın aynı aralıktaki sonucu (%).
    during_drawdowns: tuple[float, ...]
    #: Son kapanıştaki karar (yalnızca bilgi; öneri değildir).
    latest_position: bool
    q_value: float = 1.0

    def verdict(self, benchmark: Performance, alpha: float = FDR_ALPHA) -> str:
        """Ön kayıttaki karar (docs/TREND-ONKAYIT.md §5)."""
        better = (
            self.performance.sharpe > benchmark.sharpe
            and self.performance.max_drawdown_pct < benchmark.max_drawdown_pct
        )
        if better and self.q_value <= alpha:
            return "Geçti"
        if better:
            return "Belirsiz"
        return "Geçmedi"


@dataclass(frozen=True)
class SymbolResult:
    symbol: str
    first_open_time: int
    start_open_time: int
    end_open_time: int
    cost: float
    benchmark: Performance
    drawdowns: tuple[Drawdown, ...]
    rules: tuple[RuleResult, ...]


def evaluation_arrays(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Değerlendirme dönemindeki günlük getiriler ve bu getirileri alan kararların günleri."""
    close = frame["close"].to_numpy(dtype=float)
    returns = close[EVALUATION_START + 1 :] / close[EVALUATION_START:-1] - 1.0
    return returns, np.arange(EVALUATION_START, close.size - 1)


def evaluate_symbol(
    frame: pd.DataFrame,
    *,
    symbol: str,
    cost: float,
    resamples: int = RESAMPLES,
    block: float = BLOCK_DAYS,
    seed: int = SEED,
    rules: Sequence[TrendRule] = RULES,
    on_progress: Callable[[int, int], None] | None = None,
) -> SymbolResult:
    """Bir coinin günlük mumlarında beş kuralı ve al-ve-tut'u ölçer.

    ``frame`` yalnızca kapanmış mumlardan oluşmalıdır. q-değerleri burada
    değil, iki coin birlikte :func:`apply_correction` ile hesaplanır: aile
    on sınamadır.
    """
    frame = frame.sort_values("open_time").reset_index(drop=True)
    if len(frame) < EVALUATION_START + 2 * DAYS_PER_YEAR:
        raise ValueError(
            f"{symbol}: {len(frame)} günlük mum var; ısınma ({EVALUATION_START} gün) "
            f"sonrasında en az iki yıl gerekir."
        )
    times = frame["open_time"].to_numpy()
    returns, decision_days = evaluation_arrays(frame)
    all_in = np.ones(returns.size, dtype=bool)
    benchmark = measure(all_in, returns, cost)
    benchmark_net = daily_net_returns(all_in, returns, cost)
    benchmark_equity = equity_curve(benchmark_net)
    drawdowns = tuple(
        replace(item, peak_time=int(times[EVALUATION_START + item.peak]),
                trough_time=int(times[EVALUATION_START + item.trough]))
        for item in largest_drawdowns(benchmark_equity)
    )

    decisions = [rule.positions(frame) for rule in rules]
    held = [item[decision_days] for item in decisions]
    nets = [daily_net_returns(item, returns, cost) for item in held]
    tests = difference_tests(nets, benchmark_net, resamples=resamples, block=block,
                             seed=seed, on_progress=on_progress)

    results: list[RuleResult] = []
    for rule, choice, position, net, test in zip(rules, decisions, held, nets, tests,
                                                 strict=True):
        equity = equity_curve(net)
        # Bir gün geç: kararı ertesi gün uygulamak (karar dışı dayanıklılık).
        delayed = choice[decision_days - 1]
        results.append(
            RuleResult(
                rule=rule,
                performance=measure(position, returns, cost),
                delayed=measure(delayed, returns, cost),
                test=test,
                during_drawdowns=tuple(
                    (float(equity[item.trough] / equity[item.peak]) - 1.0) * 100.0
                    for item in drawdowns
                ),
                latest_position=bool(choice[-1]),
            )
        )

    return SymbolResult(
        symbol=symbol,
        first_open_time=int(times[0]),
        start_open_time=int(times[EVALUATION_START]),
        end_open_time=int(times[-1]),
        cost=cost,
        benchmark=benchmark,
        drawdowns=drawdowns,
        rules=tuple(results),
    )


def apply_correction(
    symbols: Sequence[SymbolResult], alpha: float = FDR_ALPHA
) -> list[SymbolResult]:
    """Bütün coinlerin bütün kurallarını tek aile olarak düzeltir (Benjamini–Hochberg)."""
    p_values = np.array([item.test.p_value for result in symbols for item in result.rules])
    _, q_values = benjamini_hochberg(p_values, alpha)
    corrected: list[SymbolResult] = []
    position = 0
    for result in symbols:
        rules: list[RuleResult] = []
        for item in result.rules:
            rules.append(replace(item, q_value=float(q_values[position])))
            position += 1
        corrected.append(replace(result, rules=tuple(rules)))
    return corrected
