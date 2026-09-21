"""Fizibilite taraması testleri."""
from decimal import Decimal

import numpy as np
import pandas as pd

from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.research.feasibility import atr_percent, scan_interval, true_range


def series(count: int, *, sigma_pct: float, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, sigma_pct / 100, count)
    close = 43_000 * np.exp(np.cumsum(returns))
    half = np.abs(rng.normal(0, sigma_pct / 200, count))
    return pd.DataFrame({
        "open_time": np.arange(count) * 60_000,
        "open": close, "high": close * (1 + half),
        "low": close * (1 - half), "close": close,
        "volume": np.ones(count), "is_closed": True,
    })


def threshold(maker="0.001", taker="0.001"):
    trip = round_trip_for(
        flat_table("X", maker, taker),
        entry_liquidity=Liquidity.MAKER, exit_liquidity=Liquidity.TAKER,
    )
    return minimum_meaningful_target(
        trip, spread_pct="0.01", slippage_pct="0.02", safety_pct="0.05"
    )


def test_true_range_onceki_kapanisi_hesaba_katar():
    frame = pd.DataFrame({"high": [10.0, 12.0], "low": [9.0, 11.5], "close": [9.5, 11.8]})
    tr = true_range(frame)
    # İkinci mumda boşluk var: |12 - 9.5| = 2.5, mum içi aralık ise 0.5
    assert tr.iloc[1] == 2.5


def test_dusuk_volatilite_uygun_degil():
    # 1m'de tipik BTC hareketi maliyeti karşılamaz (FAZ0-MIMARI.md Risk #5)
    row = scan_interval(series(3000, sigma_pct=0.02), symbol="X",
                        interval="1m", threshold=threshold())
    assert row.ratio < Decimal("1")
    assert row.verdict == "UYGUN DEĞİL"
    assert "karşılamıyor" in row.explanation_tr


def test_yuksek_volatilite_uygun():
    row = scan_interval(series(3000, sigma_pct=0.5), symbol="X",
                        interval="1h", threshold=threshold())
    assert row.ratio >= Decimal("2")
    assert row.verdict == "UYGUN"


def test_dusuk_komisyon_orani_yukseltir():
    data = series(3000, sigma_pct=0.1)
    pahali = scan_interval(data, symbol="X", interval="5m", threshold=threshold("0.001", "0.001"))
    ucuz = scan_interval(data, symbol="X", interval="5m", threshold=threshold("0.0002", "0.0004"))
    assert ucuz.ratio > pahali.ratio


def test_yetersiz_veri_veri_yok_der():
    row = scan_interval(series(5, sigma_pct=0.1), symbol="X",
                        interval="1m", threshold=threshold())
    assert row.candles == 5
    assert row.ratio == Decimal("0")


def test_yalnizca_kapanmis_mumlar_kullanilir():
    data = series(1000, sigma_pct=0.1)
    data.loc[data.index[-50:], "is_closed"] = False
    row = scan_interval(data, symbol="X", interval="1m", threshold=threshold())
    assert row.candles == 950


def test_gun_basina_mum_sayisi_dogrudur():
    row = scan_interval(series(1000, sigma_pct=0.1), symbol="X",
                        interval="15m", threshold=threshold())
    assert row.candles_per_day == 96
