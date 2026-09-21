"""Özellik katmanı testleri: formasyonlar, göstergeler, ısınma."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from veri_uret import random_walk

from albsat.features import FAMILIES_TR, build_features
from albsat.features.candles import (
    bearish_engulfing,
    bullish_engulfing,
    doji,
    hammer,
    inside_bar,
    outside_bar,
    shooting_star,
    three_down,
    three_up,
)
from albsat.features.indicators import adx, bollinger, ema, rolling_percentile_rank, rsi


def bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    array = np.array(rows, dtype=float)
    count = len(rows)
    volume = np.full(count, 100.0)
    return pd.DataFrame(
        {
            "open_time": np.arange(count) * 900_000 + 1_740_000_000_000,
            "open": array[:, 0],
            "high": array[:, 1],
            "low": array[:, 2],
            "close": array[:, 3],
            "volume": volume,
            "quote_volume": volume * array[:, 3],
            "trades": np.full(count, 10, dtype="int64"),
            "taker_buy_base": volume * 0.5,
            "taker_buy_quote": volume * array[:, 3] * 0.5,
            "is_closed": True,
        }
    )


def test_yukselis_yutan_mum():
    frame = bars([(100, 101, 96, 97), (96, 103, 95, 102)])
    assert bullish_engulfing(frame).tolist() == [False, True]
    assert bearish_engulfing(frame).tolist() == [False, False]


def test_dusus_yutan_mum():
    frame = bars([(97, 103, 96, 102), (103, 104, 95, 96)])
    assert bearish_engulfing(frame).tolist() == [False, True]


def test_cekic_ve_kayan_yildiz():
    cekic = bars([(100, 100.5, 90, 100)])
    assert hammer(cekic).iloc[0]
    assert not shooting_star(cekic).iloc[0]

    yildiz = bars([(100, 110, 99.5, 100)])
    assert shooting_star(yildiz).iloc[0]
    assert not hammer(yildiz).iloc[0]


def test_doji():
    assert doji(bars([(100, 105, 95, 100.1)])).iloc[0]
    assert not doji(bars([(100, 105, 95, 104)])).iloc[0]


def test_ic_ve_dis_mum():
    frame = bars([(100, 110, 90, 105), (101, 108, 95, 102), (100, 120, 80, 110)])
    assert inside_bar(frame).tolist() == [False, True, False]
    assert outside_bar(frame).tolist() == [False, False, True]


def test_ardisik_mumlar():
    frame = bars([(100, 101, 99, 101), (101, 102, 100, 102), (102, 103, 101, 103)])
    assert three_up(frame).tolist() == [False, False, True]
    assert not three_down(frame).any()


def test_rsi_sinirlari():
    yukselen = bars([(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(40)])
    values = rsi(yukselen, 14).dropna()
    assert values.between(0, 100).all()
    # Kesintisiz yükselişte RSI üst sınıra yapışır.
    assert values.iloc[-1] > 95


def test_ema_isinma_nan():
    frame = random_walk(60, seed=3)
    values = ema(frame["close"], 50)
    assert values.iloc[:49].isna().all()
    assert values.iloc[49:].notna().all()


def test_bollinger_bant_sirasi():
    frame = random_walk(200, seed=4)
    bands = bollinger(frame).dropna()
    assert (bands["upper"] >= bands["middle"]).all()
    assert (bands["middle"] >= bands["lower"]).all()
    assert (bands["width_pct"] >= 0).all()


def test_adx_araligi():
    frame = random_walk(500, seed=5)
    values = adx(frame)["adx"].dropna()
    assert values.between(0, 100).all()


def test_yuzdelik_sira_gecmise_bakiyor():
    """Sürekli artan seride her değer kendi penceresinin en yükseğidir."""
    series = pd.Series(np.arange(100, dtype=float))
    rank = rolling_percentile_rank(series, 20).dropna()
    assert (rank == 1.0).all()


def test_ozellik_tablosu_boole_ve_eksiksiz():
    frame = random_walk(1_500, seed=6)
    features = build_features(frame, interval="15m")
    assert len(features.names) > 40
    assert all(features.frame[name].dtype == bool for name in features.names)
    assert not features.frame.isna().any().any()
    assert set(features.families.values()) <= set(FAMILIES_TR)


def test_btc_baglami_aile_ekliyor():
    frame = random_walk(1_500, seed=6)
    context = random_walk(1_500, seed=7)
    without = build_features(frame, interval="15m")
    with_context = build_features(frame, interval="15m", context=context)
    added = set(with_context.names) - set(without.names)
    assert added == {"btc_yukari", "btc_asagi", "btc_oynak", "btc_yukselis_dizilimi"}


def test_isinma_satirlari_isaretli():
    frame = random_walk(1_000, seed=8)
    features = build_features(frame, interval="15m")
    ready = features.ready
    assert not ready.iloc[: features.warmup].any()
    assert ready.iloc[features.warmup :].all()


def test_kisa_seride_isinma_tum_satirlari_kapsar():
    frame = random_walk(30, seed=9)
    features = build_features(frame, interval="15m")
    assert features.warmup == 30
    assert not features.ready.any()


def test_bos_cerceve_cokmuyor():
    empty = random_walk(10, seed=1).iloc[:0]
    features = build_features(empty, interval="15m")
    assert features.names == ()
    assert features.warmup == 0


def test_kapanmamis_mum_tabloya_girmiyor():
    """SPEC.md §11: kapanmamış mum hiçbir hesaba giremez."""
    frame = random_walk(400, seed=2)
    frame.loc[frame.index[-1], "is_closed"] = False
    features = build_features(frame, interval="15m")
    assert len(features.frame) == len(frame) - 1


def test_aciklamalar_turkce_ve_dolu():
    frame = random_walk(1_000, seed=11)
    features = build_features(frame, interval="15m")
    for name in features.names:
        description = features.describe(name)
        assert description and description != name
        assert features.family_of(name) in FAMILIES_TR


@pytest.mark.parametrize("interval", ["15m", "1h"])
def test_zaman_ozellikleri_periyottan_bagimsiz(interval):
    step = 900_000 if interval == "15m" else 3_600_000
    frame = random_walk(1_000, seed=12, step_ms=step)
    features = build_features(frame, interval=interval)
    assert features.frame["seans_asya"].any()
    assert features.frame["hafta_sonu"].any()
