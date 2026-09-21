"""Look-ahead (geleceğe bakma) ve repaint testleri.

SPEC.md §10, Faz 2 kabul kriteri: **"Look-ahead testleri geçer."**

İki bağımsız yoldan kanıtlanıyor:

1. **Geleceği bozma.** Veri belli bir mumdan sonra tanınmaz hale getirilir.
   O mumdan önceki hiçbir özellik, hiçbir sonuç değişmemelidir. Değişiyorsa
   bir yerde geleceğe bakılıyordur.
2. **Kesip yeniden hesaplama (repaint).** Seri kısaltılıp özellikler yeniden
   hesaplanır. Ortak bölgedeki değerler birebir aynı olmalıdır. Farklıysa
   gösterge, sonradan gelen veriyle geçmişi "yeniden boyuyordur" — canlıda
   göründüğü gibi davranmayacağının işareti.

Bu iki test bu depodaki en önemli testlerdir. Geçmedikleri sürece hiçbir
örüntü sonucuna güvenilmemelidir.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from veri_uret import poison_future, random_walk

from albsat.core.costs import minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.features import build_features
from albsat.features.indicators import (
    adx,
    atr_percent,
    bollinger,
    ema,
    macd,
    rolling_percentile_rank,
    rolling_vwap,
    rsi,
)
from albsat.research.eventstudy import OutcomeConfig, build_outcomes

CUT = 1_200
COUNT = 2_000


@pytest.fixture(scope="module")
def frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    clean = random_walk(COUNT, seed=5)
    return clean, poison_future(clean, CUT)


def _trips():
    table = flat_table("BTCUSDT", "0.001", "0.001")
    to_target = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.MAKER
    )
    to_stop = round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )
    return to_target, to_stop


def test_ozellikler_gelecekten_etkilenmiyor(frames):
    """Geleceği bozmak, geçmişteki tek bir özelliği bile değiştirmemeli."""
    clean, poisoned = frames
    before = build_features(clean, interval="15m").frame.iloc[:CUT]
    after = build_features(poisoned, interval="15m").frame.iloc[:CUT]

    differing = [name for name in before.columns if not before[name].equals(after[name])]
    assert differing == [], f"Geleceğe bakan özellikler: {differing}"


def test_btc_baglami_da_gelecekten_etkilenmiyor(frames):
    """BTC etkisi ailesi de aynı kurala tabi."""
    clean, poisoned = frames
    context_clean = random_walk(COUNT, seed=6)
    context_poisoned = poison_future(context_clean, CUT)

    before = build_features(clean, interval="15m", context=context_clean).frame.iloc[:CUT]
    after = build_features(
        poisoned, interval="15m", context=context_poisoned
    ).frame.iloc[:CUT]

    differing = [name for name in before.columns if not before[name].equals(after[name])]
    assert differing == [], f"Geleceğe bakan özellikler: {differing}"


@pytest.mark.parametrize(
    "name,function",
    [
        ("rsi", lambda f: rsi(f, 14)),
        ("atr_percent", lambda f: atr_percent(f, 14)),
        ("macd", lambda f: macd(f)["histogram"]),
        ("bollinger", lambda f: bollinger(f)["width_pct"]),
        ("adx", lambda f: adx(f)["adx"]),
        ("vwap", lambda f: rolling_vwap(f, 20)),
        ("ema", lambda f: ema(f["close"], 50)),
        ("yuzdelik", lambda f: rolling_percentile_rank(f["close"], 200)),
    ],
)
def test_gostergeler_repaint_etmiyor(frames, name, function):
    """Seriyi kesip yeniden hesaplamak, ortak bölgede aynı değerleri vermeli."""
    clean, _ = frames
    full = function(clean).to_numpy()[:CUT]
    truncated = function(clean.iloc[:CUT].reset_index(drop=True)).to_numpy()

    np.testing.assert_allclose(
        full, truncated, rtol=1e-12, atol=1e-12, equal_nan=True,
        err_msg=f"{name} sonradan gelen veriyle geçmişi değiştiriyor",
    )


def test_ozellik_tablosu_repaint_etmiyor(frames):
    """Tüm özellik tablosu için aynı kesme testi."""
    clean, _ = frames
    full = build_features(clean, interval="15m").frame.iloc[:CUT].reset_index(drop=True)
    truncated = build_features(
        clean.iloc[:CUT].reset_index(drop=True), interval="15m"
    ).frame

    differing = [name for name in full.columns if not full[name].equals(truncated[name])]
    assert differing == [], f"Repaint eden özellikler: {differing}"


@pytest.mark.parametrize("horizon", [2, 3, 4])
def test_sonuclar_yalnizca_pencere_kadar_ileriye_bakiyor(frames, horizon):
    """Bir sonuç en fazla ``horizon`` mum ileriye bakmalı, bir mum fazlasına değil."""
    clean, poisoned = frames
    to_target, to_stop = _trips()
    config = OutcomeConfig(horizon=horizon, target_atr=1.5, stop_atr=1.0)

    before = build_outcomes(
        clean, config=config, trip_to_target=to_target, trip_to_stop=to_stop
    )
    after = build_outcomes(
        poisoned, config=config, trip_to_target=to_target, trip_to_stop=to_stop
    )

    # Giriş bir sonraki mumun açılışı olduğu için ``i`` satırı en son
    # ``i + horizon`` numaralı muma bakar.
    limit = CUT - horizon - 1
    np.testing.assert_allclose(
        before.net_pct[:limit], after.net_pct[:limit], rtol=1e-12, equal_nan=True
    )
    np.testing.assert_array_equal(before.exit_reason[:limit], after.exit_reason[:limit])
    np.testing.assert_array_equal(before.bars_held[:limit], after.bars_held[:limit])

    # Sınırın hemen ötesi ise gerçekten değişmeli; aksi halde test hiçbir şey
    # kanıtlamıyor demektir (bozma işlemi etkisiz kalmış olurdu).
    tail = slice(CUT - 1, CUT + horizon)
    assert not np.allclose(
        np.nan_to_num(before.net_pct[tail]), np.nan_to_num(after.net_pct[tail])
    ), "Bozma işlemi hiçbir şeyi değiştirmemiş; test geçersiz"


def test_giris_sinyal_mumunun_kapanisindan_degil():
    """Giriş fiyatı, sinyal mumunun kapanışı değil, SONRAKİ mumun açılışıdır.

    Sentetik seride açılış bir öncekinin kapanışına eşit olduğu için bu fark
    görünmez; test bu yüzden boşluklu (gap) bir seri kuruyor. Fark küçük
    görünse de yönü hep aynıdır: sinyal mumunun kapanışından girdiğini
    varsayan bir backtest, gerçekte yakalanamayan bir fiyattan girmiş olur.
    """
    to_target, to_stop = _trips()
    count = 40
    close = np.full(count, 100.0)
    # Her mum bir öncekinin kapanışının %1 üstünde açılıyor: boşluk.
    open_ = close * 1.01
    frame = pd.DataFrame(
        {
            "open_time": np.arange(count) * 900_000 + 1_740_000_000_000,
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": np.full(count, 10.0),
            "quote_volume": np.full(count, 1_000.0),
            "trades": np.full(count, 5, dtype="int64"),
            "taker_buy_base": np.full(count, 5.0),
            "taker_buy_quote": np.full(count, 500.0),
            "is_closed": True,
        }
    )
    outcomes = build_outcomes(
        frame,
        config=OutcomeConfig(horizon=3),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
    )
    index = int(np.flatnonzero(outcomes.eligible)[0])
    assert outcomes.entry_price[index] == pytest.approx(frame["open"].iloc[index + 1])
    assert outcomes.entry_price[index] != pytest.approx(frame["close"].iloc[index])


def test_son_mumlar_uygun_sayilmaz(frames):
    """İleride yeterli mum kalmayan satırlar olay sayılmamalı."""
    clean, _ = frames
    to_target, to_stop = _trips()
    horizon = 4
    outcomes = build_outcomes(
        clean,
        config=OutcomeConfig(horizon=horizon),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
    )
    assert not outcomes.eligible[-horizon:].any()


def test_maliyet_esigi_uygulaniyor():
    """Hedefi maliyet eşiğinin altında kalan mumlar elenmeli (SPEC.md §4.3)."""
    calm = random_walk(1_000, seed=3, sigma_pct=0.02)
    to_target, to_stop = _trips()
    threshold = minimum_meaningful_target(
        to_stop, spread_pct="0.01", slippage_pct="0.02", safety_pct="0.05"
    )
    outcomes = build_outcomes(
        calm,
        config=OutcomeConfig(horizon=3, target_atr=1.0),
        trip_to_target=to_target,
        trip_to_stop=to_stop,
        threshold=threshold,
    )
    assert outcomes.rejected_by_threshold > 0
    assert outcomes.eligible_count == 0
