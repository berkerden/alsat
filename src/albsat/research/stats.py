"""İstatistiksel sağlamlık araçları (SPEC.md §4.2 "istatistiksel sağlamlık").

Üç soruya cevap verir:

1. **Bu sayı gürültü olabilir mi?** Bootstrap güven aralığı. İşlem getirileri
   normal dağılmaz (kalın kuyruklu, çarpık); t-testi burada yanıltır, yeniden
   örnekleme yanıltmaz.
2. **Yüzlerce örüntü denedik; en iyisi şans eseri mi iyi görünüyor?**
   Benjamini–Hochberg yanlış keşif oranı düzeltmesi. 500 örüntüyü %5
   eşiğiyle test edersek hiçbiri gerçek olmasa bile ortalama 25 tanesi
   "anlamlı" çıkar. Düzeltme bunu keser.
3. **Strateji düzeyinde Sharpe abartılı mı?** Deflated Sharpe (Bailey &
   López de Prado): denenen strateji sayısını, örneklem büyüklüğünü ve
   getirilerin çarpıklığını hesaba katar.

Hepsi saf ``numpy`` ile yazıldı; ``scipy`` bağımlılığı eklemiyoruz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: Euler–Mascheroni sabiti (deflated Sharpe için).
EULER_MASCHERONI = 0.5772156649015329


def normal_cdf(value: float) -> float:
    """Standart normal birikimli dağılım."""
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def normal_quantile(probability: float) -> float:
    """Standart normal ters birikimli dağılım (Acklam yaklaşımı, ~1e-9 hata)."""
    if not 0.0 < probability < 1.0:
        raise ValueError("olasılık 0 ile 1 arasında olmalı")

    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)

    low, high = 0.02425, 1.0 - 0.02425
    if probability < low:
        q = math.sqrt(-2.0 * math.log(probability))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if probability > high:
        q = math.sqrt(-2.0 * math.log(1.0 - probability))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    q = probability - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)


@dataclass(frozen=True)
class BootstrapResult:
    """Ortalamanın yeniden örneklemeyle ölçülmüş belirsizliği."""

    mean: float
    low: float
    high: float
    p_value: float
    iterations: int

    @property
    def significant(self) -> bool:
        """Güven aralığının tamamı sıfırın üstünde mi?"""
        return self.low > 0.0

    @property
    def summary_tr(self) -> str:
        return (
            f"ortalama %{self.mean:+.4f} "
            f"(güven aralığı %{self.low:+.4f} … %{self.high:+.4f}, p={self.p_value:.4f})"
        )


def bootstrap_mean(
    values: np.ndarray,
    *,
    iterations: int = 2000,
    alpha: float = 0.05,
    seed: int = 20260921,
) -> BootstrapResult:
    """Ortalama için güven aralığı ve "ortalama sıfırdan büyük mü" p-değeri.

    p-değeri H0 "beklenen değer sıfır" varsayımı altında hesaplanır: örneklem
    sıfıra ortalanır, yeniden örneklenir, gözlenen ortalamaya ulaşma sıklığı
    sayılır. Tek yönlüdür, çünkü soru "kâr ediyor mu", "farklı mı" değil.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    count = int(values.size)
    if count < 2:
        mean = float(values.mean()) if count else 0.0
        return BootstrapResult(mean, mean, mean, 1.0, 0)

    generator = np.random.default_rng(seed)
    draws = generator.integers(0, count, size=(iterations, count))
    means = values[draws].mean(axis=1)

    observed = float(values.mean())
    low, high = np.percentile(means, [alpha / 2 * 100, (1 - alpha / 2) * 100])

    centred = values - observed
    null_means = centred[draws].mean(axis=1)
    # +1 düzeltmesi: hiçbir yeniden örnekleme ulaşamazsa p sıfır değil,
    # "iterations kadar denemede görülmedi" demektir.
    p_value = float((np.count_nonzero(null_means >= observed) + 1) / (iterations + 1))

    return BootstrapResult(observed, float(low), float(high), p_value, iterations)


def benjamini_hochberg(
    p_values: np.ndarray, alpha: float = 0.10, *, total_tests: int | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Yanlış keşif oranı (FDR) düzeltmesi.

    Döndürür: ``(kabul_edilen, q_degerleri)``. ``q`` bir örüntüyü kabul etmek
    için göze alınması gereken en küçük yanlış keşif oranıdır; ham p'den
    büyük veya ona eşittir.

    ``total_tests``, p-değeri hesaplanmamış denemeleri de saymak içindir.
    Tarama binlerce örüntüyü eler ve yalnızca umut verenlerin p-değerini
    hesaplar; düzeltme yine de **denenen toplam sayıya** göre yapılmalıdır,
    yoksa eleme işleminin kendisi sonucu iyimser gösterir. Elenenler tek
    yönlü testte zaten en büyük p-değerlerine sahip olduğu için sıralamanın
    başını değiştirmezler.
    """
    p_values = np.asarray(p_values, dtype=float)
    count = int(p_values.size)
    if count == 0:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=float)
    tests = max(count, int(total_tests or count))

    order = np.argsort(p_values)
    ranks = np.arange(1, count + 1)
    ordered = p_values[order]

    # q, sıralamada geriye doğru kümülatif minimumla düzeltilir; aksi halde
    # sıralı q değerleri azalabilir ve anlamını yitirir.
    raw = ordered * tests / ranks
    q_ordered = np.minimum.accumulate(raw[::-1])[::-1]
    q_ordered = np.minimum(q_ordered, 1.0)

    q_values = np.empty(count, dtype=float)
    q_values[order] = q_ordered
    return q_values <= alpha, q_values


def sharpe_ratio(returns: np.ndarray, *, periods_per_year: float = 0.0) -> float:
    """Getiri dizisinin Sharpe oranı.

    ``periods_per_year`` verilirse yıllıklandırılır. Risksiz faiz sıfır
    kabul edilir; kısa vadeli kripto işlemlerinde etkisi ihmal edilebilir
    ve varsayımı açıkça yazmak, gizlice eklemekten iyidir.
    """
    returns = np.asarray(returns, dtype=float)
    returns = returns[np.isfinite(returns)]
    if returns.size < 2:
        return 0.0
    deviation = float(returns.std(ddof=1))
    if deviation == 0.0:
        return 0.0
    ratio = float(returns.mean()) / deviation
    if periods_per_year > 0:
        ratio *= math.sqrt(periods_per_year)
    return ratio


def expected_maximum_sharpe(trials: int, sharpe_variance: float) -> float:
    """Hiçbiri gerçek olmayan ``trials`` denemede beklenen en yüksek Sharpe.

    Bailey & López de Prado'nun yaklaşımı. Yüz örüntü denenince en iyisinin
    Sharpe'ı, hepsi değersiz olsa bile sıfır çıkmaz; bu fonksiyon o "bedava"
    Sharpe'ı verir ve gerçek bulgunun onu aşması beklenir.
    """
    if trials < 2 or sharpe_variance <= 0:
        return 0.0
    deviation = math.sqrt(sharpe_variance)
    first = normal_quantile(1.0 - 1.0 / trials)
    second = normal_quantile(1.0 - 1.0 / (trials * math.e))
    return deviation * ((1.0 - EULER_MASCHERONI) * first + EULER_MASCHERONI * second)


def deflated_sharpe_ratio(
    returns: np.ndarray, *, trials: int, sharpe_variance: float | None = None
) -> float:
    """Denenen strateji sayısına göre düzeltilmiş Sharpe güvenilirliği (0–1).

    Sonuç bir olasılıktır: "bu Sharpe, bu kadar deneme yapıldıktan sonra
    hâlâ gerçek olma ihtimali". 0,95 üstü güçlü, 0,5 altı zayıftır.
    """
    returns = np.asarray(returns, dtype=float)
    returns = returns[np.isfinite(returns)]
    count = int(returns.size)
    if count < 8:
        return 0.0

    observed = sharpe_ratio(returns)
    centred = returns - returns.mean()
    deviation = float(returns.std(ddof=1))
    if deviation == 0.0:
        return 0.0
    skew = float((centred**3).mean() / deviation**3)
    kurtosis = float((centred**4).mean() / deviation**4)

    if sharpe_variance is None:
        # Tek bir taramada denemelerin Sharpe varyansı bilinmez; örneklem
        # büyüklüğünden gelen teorik varyans makul bir alt sınırdır.
        sharpe_variance = (1.0 + 0.5 * observed**2) / count
    benchmark = expected_maximum_sharpe(trials, sharpe_variance)

    denominator = 1.0 - skew * observed + (kurtosis - 1.0) / 4.0 * observed**2
    if denominator <= 0:
        return 0.0
    statistic = (observed - benchmark) * math.sqrt(count - 1) / math.sqrt(denominator)
    return normal_cdf(statistic)


def max_drawdown_pct(equity: np.ndarray) -> float:
    """Sermaye eğrisindeki en büyük tepe-dip düşüşü, yüzde olarak (pozitif sayı)."""
    equity = np.asarray(equity, dtype=float)
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    with np.errstate(invalid="ignore", divide="ignore"):
        drawdown = (equity - peak) / peak
    return float(-np.nanmin(drawdown) * 100.0)
