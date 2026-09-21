"""Backtest motoru ve kıyas ölçütü testleri."""

from __future__ import annotations

import numpy as np
import pytest
from veri_uret import planted_signal, random_walk

from albsat.backtest import run, summarize
from albsat.backtest.benchmarks import (
    buy_and_hold,
    compare_with_random_entries,
    random_entry_backtest,
    random_entry_summary,
)
from albsat.core.costs import round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.research.eventstudy import OutcomeConfig, build_outcomes


def trips():
    table = flat_table("BTCUSDT", "0.001", "0.001")
    return (
        round_trip_for(table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.MAKER),
        round_trip_for(table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER),
    )


def outcomes_for(frame, horizon=3):
    to_target, to_stop = trips()
    return build_outcomes(
        frame,
        config=OutcomeConfig(horizon=horizon, target_atr=1.5, stop_atr=1.0),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
    )


def test_pozisyon_acikken_sinyal_atlaniyor():
    """Aynı anda tek pozisyon: üst üste gelen sinyaller işleme dönüşmemeli."""
    frame = random_walk(2_000, seed=13)
    outcomes = outcomes_for(frame, horizon=4)
    signals = outcomes.eligible.copy()  # her uygun mumda sinyal

    result = run(frame, outcomes, signals)
    assert result.signals_skipped_busy > 0
    assert result.trade_count < int(signals.sum())

    # İşlemler zaman içinde örtüşmemeli.
    for earlier, later in zip(result.trades, result.trades[1:], strict=False):
        assert later.entry_index >= earlier.entry_index + earlier.bars_held


def test_sermaye_bilesik_buyuyor():
    frame = random_walk(1_000, seed=14)
    outcomes = outcomes_for(frame)
    signals = np.zeros(len(frame), dtype=bool)
    chosen = np.flatnonzero(outcomes.eligible)[:20]
    signals[chosen] = True

    result = run(frame, outcomes, signals, start_equity=100.0)
    expected = 100.0
    for trade in result.trades:
        expected *= 1.0 + trade.net_pct / 100.0
    assert result.equity[-1] == pytest.approx(expected)
    assert result.total_return_pct == pytest.approx((expected / 100.0 - 1) * 100.0)


def test_sinyalsiz_backtest_bos_sonuc():
    frame = random_walk(500, seed=15)
    outcomes = outcomes_for(frame)
    result = run(frame, outcomes, np.zeros(len(frame), dtype=bool))
    assert result.trade_count == 0
    assert result.total_return_pct == 0.0
    assert result.max_drawdown_pct == 0.0
    assert result.profit_factor == 0.0
    assert result.annual_sharpe == 0.0


def test_soguma_suresi_islem_sayisini_azaltiyor():
    frame = random_walk(3_000, seed=16)
    outcomes = outcomes_for(frame)
    signals = outcomes.eligible.copy()
    without = run(frame, outcomes, signals)
    with_cooldown = run(frame, outcomes, signals, cooldown_bars=10)
    assert with_cooldown.trade_count < without.trade_count


def test_coklu_pozisyon_henuz_desteklenmiyor():
    frame = random_walk(200, seed=17)
    outcomes = outcomes_for(frame)
    with pytest.raises(NotImplementedError):
        run(frame, outcomes, outcomes.eligible, max_positions=3)


def test_kar_faktoru_ve_dusus():
    frame = random_walk(2_000, seed=18)
    outcomes = outcomes_for(frame)
    result = run(frame, outcomes, outcomes.eligible)
    assert result.trade_count > 0
    assert result.max_drawdown_pct >= 0
    assert result.profit_factor >= 0
    assert 0.0 <= result.hit_rate <= 1.0
    assert len(result.trade_frame()) == result.trade_count
    assert summarize(result, "deneme").trades == result.trade_count


def test_al_ve_tut_komisyonu_dusuyor():
    frame = random_walk(500, seed=19)
    _, to_stop = trips()
    hold = buy_and_hold(frame, to_stop)
    assert hold.net_pct < hold.gross_pct
    assert hold.start_price == pytest.approx(frame["open"].iloc[0])
    assert hold.end_price == pytest.approx(frame["close"].iloc[-1])


def test_al_ve_tut_kisa_seride_sifir():
    frame = random_walk(500, seed=19).iloc[:1]
    _, to_stop = trips()
    assert buy_and_hold(frame, to_stop).net_pct == 0.0


def test_rastgele_giris_gomulu_sinyalden_kotu():
    """Gerçek kenar, rastgele girişlerin büyük çoğunluğunu geçmeli."""
    frame, trigger = planted_signal(9_000, drift_pct=1.2)
    outcomes = outcomes_for(frame)
    comparison = compare_with_random_entries(outcomes, trigger, repeats=800)
    assert comparison.beats_random
    assert comparison.pattern_mean_pct > comparison.random_mean_pct


def test_rastgele_giris_rastgele_veride_ayirt_edilemiyor():
    frame = random_walk(9_000, seed=20)
    outcomes = outcomes_for(frame)
    mask = np.zeros(len(frame), dtype=bool)
    mask[np.flatnonzero(outcomes.eligible)[::7]] = True
    comparison = compare_with_random_entries(outcomes, mask, repeats=800)
    assert not comparison.beats_random


def test_rastgele_giris_backtestleri_ayni_motoru_kullaniyor():
    frame = random_walk(3_000, seed=22)
    outcomes = outcomes_for(frame)
    results = random_entry_backtest(frame, outcomes, signal_count=30, repeats=25)
    assert len(results) == 25
    assert all(result.trade_count > 0 for result in results)
    distribution = random_entry_summary(results)
    assert distribution["p05"] <= distribution["medyan"] <= distribution["p95"]


def test_rastgele_giris_bos_durumlar():
    frame = random_walk(500, seed=23)
    outcomes = outcomes_for(frame)
    assert random_entry_backtest(frame, outcomes, signal_count=0) == []
    assert random_entry_summary([]) == {}
    bos = compare_with_random_entries(outcomes, np.zeros(len(frame), dtype=bool))
    assert bos.sample_size == 0
    assert "yeterli olay yok" in bos.summary_tr
