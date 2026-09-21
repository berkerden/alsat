"""Olay çalışması testleri: çıkış kuralları, maliyet ve Decimal tutarlılığı."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from veri_uret import random_walk

from albsat.core.costs import FeePayment, round_trip_for
from albsat.core.fees import Discount, Liquidity, flat_table
from albsat.research.eventstudy import (
    EXIT_STOP,
    EXIT_TARGET,
    EXIT_TIMEOUT,
    OutcomeConfig,
    build_outcomes,
    forward_window_profile,
    independent_indices,
    net_margin_pct_array,
    summarize,
)


def trips():
    table = flat_table("BTCUSDT", "0.001", "0.001")
    return (
        round_trip_for(table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.MAKER),
        round_trip_for(table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER),
    )


def bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Elle kurulmuş mum serisi: (açılış, yüksek, düşük, kapanış)."""
    count = len(rows)
    array = np.array(rows, dtype=float)
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


def test_vektor_hesabi_decimal_motoruyla_ayni():
    """Hızlı ``float`` yolu, ``Decimal`` motoruyla aynı sayıyı vermeli.

    İki ayrı doğruluk kaynağı en sinsi hata türüdür: ikisi de tek başına
    doğru görünür, arada sessizce ayrışırlar.
    """
    table = flat_table("BTCUSDT", "0.00075", "0.0011")
    for entry_liquidity in (Liquidity.MAKER, Liquidity.TAKER):
        for exit_liquidity in (Liquidity.MAKER, Liquidity.TAKER):
            for discount in (False, True):
                trip = round_trip_for(
                    table,
                    entry_liquidity=entry_liquidity,
                    exit_liquidity=exit_liquidity,
                    has_discount_asset_balance=discount,
                )
                entry = np.array([100.0, 43_210.5, 0.0912])
                exit_price = np.array([101.0, 43_000.0, 0.0999])
                fast = net_margin_pct_array(trip, entry, exit_price)
                for index in range(3):
                    exact = trip.net_margin_pct(
                        Decimal(str(entry[index])), Decimal(str(exit_price[index]))
                    )
                    assert fast[index] == pytest.approx(float(exact), rel=1e-12)


def test_bnb_odemesi_farkli_formul_kullaniyor():
    base = flat_table("BTCUSDT", "0.001", "0.001")
    table = replace(
        base,
        discount=Discount(
            enabled_for_account=True, enabled_for_symbol=True, multiplier=Decimal("0.75")
        ),
    )
    trip = round_trip_for(
        table,
        entry_liquidity=Liquidity.TAKER,
        exit_liquidity=Liquidity.TAKER,
        has_discount_asset_balance=True,
    )
    assert trip.payment is FeePayment.IN_BNB
    fast = net_margin_pct_array(trip, np.array([100.0]), np.array([110.0]))
    exact = trip.net_margin_pct(Decimal("100"), Decimal("110"))
    assert fast[0] == pytest.approx(float(exact), rel=1e-12)


def test_hedefe_ulasan_islem_hedeften_cikar():
    # ATR'yi sabitlemek için önce düz, sonra yükselen bir seri.
    rows = [(100.0, 100.5, 99.5, 100.0)] * 20 + [
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 108.0, 99.9, 107.0),
        (107.0, 107.5, 106.5, 107.0),
        (107.0, 107.5, 106.5, 107.0),
    ]
    to_target, to_stop = trips()
    outcomes = build_outcomes(
        bars(rows),
        config=OutcomeConfig(horizon=3, target_atr=1.0, stop_atr=5.0, exit_slippage_pct=0.0),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
    )
    signal = 19
    assert outcomes.eligible[signal]
    assert outcomes.exit_reason[signal] == EXIT_TARGET
    assert outcomes.exit_price[signal] == pytest.approx(outcomes.target_price[signal])
    assert outcomes.bars_held[signal] == 2


def test_ayni_mumda_ikisine_de_degilirse_stop_kabul_edilir():
    """Temkinli varsayım: mum verisi sıralamayı söylemez, en kötüsü seçilir."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * 20 + [
        (100.0, 130.0, 70.0, 100.0),  # hem hedefe hem stopa değen mum
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    ]
    to_target, to_stop = trips()
    outcomes = build_outcomes(
        bars(rows),
        config=OutcomeConfig(horizon=3, target_atr=1.0, stop_atr=1.0, exit_slippage_pct=0.0),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
    )
    signal = 19
    assert outcomes.exit_reason[signal] == EXIT_STOP
    assert outcomes.net_pct[signal] < 0


def test_sure_dolarsa_son_mumun_kapanisindan_cikilir():
    rows = [(100.0, 100.2, 99.8, 100.0)] * 30
    to_target, to_stop = trips()
    outcomes = build_outcomes(
        bars(rows),
        config=OutcomeConfig(horizon=3, target_atr=5.0, stop_atr=5.0, exit_slippage_pct=0.0),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
    )
    signal = 20
    assert outcomes.exit_reason[signal] == EXIT_TIMEOUT
    assert outcomes.bars_held[signal] == 3
    assert outcomes.exit_price[signal] == pytest.approx(100.0)


def test_kayma_yalnizca_piyasa_cikisina_uygulanir():
    """Hedefe limit emirle çıkılır; kayma yalnızca stop ve süre dolumunda vardır."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * 20 + [
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 108.0, 99.9, 107.0),
        (107.0, 107.5, 106.5, 107.0),
        (107.0, 107.5, 106.5, 107.0),
    ]
    to_target, to_stop = trips()
    config = OutcomeConfig(horizon=3, target_atr=1.0, stop_atr=5.0, exit_slippage_pct=0.5)
    outcomes = build_outcomes(
        bars(rows), config=config, trip_to_target=to_target, trip_to_stop=to_stop
    )
    signal = 19
    assert outcomes.exit_reason[signal] == EXIT_TARGET
    assert outcomes.exit_price[signal] == pytest.approx(outcomes.target_price[signal])


def test_maliyet_beklenen_degeri_asagi_ceker():
    """Rastgele yürüyüşte beklenen değer, maliyet kadar negatif olmalı."""
    frame = random_walk(8_000, seed=21)
    to_target, to_stop = trips()
    outcomes = build_outcomes(
        frame,
        config=OutcomeConfig(horizon=3, target_atr=1.5, stop_atr=1.0),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
    )
    summary = summarize(outcomes, np.ones(len(frame), dtype=bool))
    assert summary.events > 1_000
    # Gidiş-dönüş komisyonu %0,2; kayma ile birlikte beklenen değer bu
    # civarda negatif olmalı. Pozitif çıkıyorsa maliyet uygulanmıyordur.
    assert -0.45 < summary.net_mean_pct < -0.10


def test_bagimsiz_olaylar_ust_uste_binmiyor():
    mask = np.zeros(20, dtype=bool)
    mask[[2, 3, 4, 9, 10, 16]] = True
    kept = independent_indices(mask, horizon=3)
    assert kept.tolist() == [2, 9, 16]
    assert np.all(np.diff(kept) > 3)


def test_ozet_bos_maskede_cokmuyor():
    frame = random_walk(500, seed=2)
    to_target, to_stop = trips()
    outcomes = build_outcomes(
        frame, config=OutcomeConfig(horizon=2),
        trip_to_target=to_target, trip_to_stop=to_stop,
    )
    summary = summarize(outcomes, np.zeros(len(frame), dtype=bool))
    assert summary.events == 0
    assert summary.net_mean_pct == 0.0


def test_ileri_pencere_profili():
    frame = random_walk(3_000, seed=8)
    profile = forward_window_profile(frame)
    assert list(profile["mum"]) == [1, 3, 5, 10, 20]
    # Rastgele yürüyüşte yukarı oranı yarıya yakın olmalı.
    assert profile["yukari_oran"].between(0.40, 0.60).all()


def test_gecersiz_yapilandirma_reddediliyor():
    with pytest.raises(ValueError):
        OutcomeConfig(horizon=0)
    with pytest.raises(ValueError):
        OutcomeConfig(horizon=3, target_atr=0.0)
