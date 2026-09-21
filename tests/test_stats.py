"""İstatistik testleri: bootstrap, çoklu test düzeltmesi, deflated Sharpe."""

from __future__ import annotations

import numpy as np
import pytest

from albsat.research.stats import (
    benjamini_hochberg,
    bootstrap_mean,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    max_drawdown_pct,
    normal_cdf,
    normal_quantile,
    sharpe_ratio,
)


def test_normal_fonksiyonlari_bilinen_degerler():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(1.959963985) == pytest.approx(0.975, abs=1e-6)
    assert normal_quantile(0.975) == pytest.approx(1.959963985, abs=1e-6)
    assert normal_quantile(0.5) == pytest.approx(0.0, abs=1e-9)
    # Ters fonksiyon gerçekten ters mi?
    for probability in (0.001, 0.02, 0.2, 0.5, 0.8, 0.98, 0.999):
        assert normal_cdf(normal_quantile(probability)) == pytest.approx(
            probability, abs=1e-6
        )


def test_normal_quantile_gecersiz_girdi():
    with pytest.raises(ValueError):
        normal_quantile(0.0)
    with pytest.raises(ValueError):
        normal_quantile(1.0)


def test_bootstrap_gurultuyu_anlamli_bulmuyor():
    values = np.random.default_rng(1).normal(0.0, 1.0, 600)
    result = bootstrap_mean(values, iterations=1_000)
    assert not result.significant
    assert result.p_value > 0.05
    assert result.low < 0 < result.high


def test_bootstrap_gercek_etkiyi_buluyor():
    values = np.random.default_rng(2).normal(0.5, 1.0, 600)
    result = bootstrap_mean(values, iterations=1_000)
    assert result.significant
    assert result.p_value < 0.01
    assert result.mean == pytest.approx(values.mean())


def test_bootstrap_p_degeri_asla_sifir_olmuyor():
    """Sıfır p-değeri "imkânsız" demektir; yeniden örnekleme bunu söyleyemez."""
    values = np.full(200, 5.0) + np.random.default_rng(3).normal(0, 0.01, 200)
    result = bootstrap_mean(values, iterations=500)
    assert result.p_value > 0.0
    assert result.p_value == pytest.approx(1 / 501, rel=1e-6)


def test_bootstrap_kucuk_orneklem():
    assert bootstrap_mean(np.array([])).iterations == 0
    assert bootstrap_mean(np.array([1.0])).mean == 1.0


def test_benjamini_hochberg_klasik_ornek():
    """Benjamini & Hochberg (1995) makalesindeki örnek: 15 testten 4'ü kabul."""
    p_values = np.array([
        0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344,
        0.0459, 0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.000,
    ])
    accepted, q_values = benjamini_hochberg(p_values, 0.05)
    assert int(accepted.sum()) == 4
    assert (q_values >= p_values).all()
    # q değerleri p sırasına göre azalmamalı.
    assert (np.diff(q_values[np.argsort(p_values)]) >= -1e-12).all()


def test_duzeltme_deneme_sayisiyla_sertlesiyor():
    p_values = np.array([0.0001, 0.0004, 0.0019, 0.0095, 0.02])
    az = benjamini_hochberg(p_values, 0.05)[0].sum()
    cok = benjamini_hochberg(p_values, 0.05, total_tests=5_000)[0].sum()
    assert cok < az


def test_benjamini_hochberg_bos_girdi():
    accepted, q_values = benjamini_hochberg(np.array([]))
    assert accepted.size == 0 and q_values.size == 0


def test_sharpe_orani():
    assert sharpe_ratio(np.array([1.0])) == 0.0
    assert sharpe_ratio(np.full(10, 2.0)) == 0.0  # sapma sıfır
    values = np.random.default_rng(4).normal(0.1, 1.0, 1_000)
    plain = sharpe_ratio(values)
    annual = sharpe_ratio(values, periods_per_year=252)
    assert annual == pytest.approx(plain * np.sqrt(252))


def test_beklenen_en_yuksek_sharpe_deneme_sayisiyla_buyuyor():
    az = expected_maximum_sharpe(10, 1 / 250)
    cok = expected_maximum_sharpe(2_000, 1 / 250)
    assert 0 < az < cok
    assert expected_maximum_sharpe(1, 1 / 250) == 0.0


def test_deflated_sharpe_gurultuyu_eliyor():
    noise = np.random.default_rng(5).normal(0.0, 1.0, 500)
    edge = np.random.default_rng(6).normal(0.4, 1.0, 500)
    assert deflated_sharpe_ratio(noise, trials=500) < 0.5
    assert deflated_sharpe_ratio(edge, trials=500) > 0.9


def test_deflated_sharpe_deneme_sayisiyla_dusuyor():
    edge = np.random.default_rng(7).normal(0.15, 1.0, 400)
    assert deflated_sharpe_ratio(edge, trials=5) > deflated_sharpe_ratio(
        edge, trials=100_000
    )


def test_deflated_sharpe_kucuk_orneklem():
    assert deflated_sharpe_ratio(np.arange(5.0), trials=10) == 0.0


def test_en_buyuk_dusus():
    assert max_drawdown_pct(np.array([100.0, 110.0, 88.0, 120.0])) == pytest.approx(20.0)
    assert max_drawdown_pct(np.array([100.0, 101.0, 102.0])) == pytest.approx(0.0)
    assert max_drawdown_pct(np.array([])) == 0.0
