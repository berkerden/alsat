"""Testler için sentetik mum üreticisi.

Gerçek piyasa verisi bu ortamdan indirilemiyor; testler bu yüzden kendi
verisini üretiyor. Üretilen seri rastgele yürüyüştür: içinde **bilerek
hiçbir örüntü yoktur**. Tarama motorunun burada bir şey bulmaması doğru
davranıştır; ``planted_signal`` ise bilerek bir kenar gömer ve motorun onu
bulabildiğini gösterir.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STEP_MS = 900_000  # 15m


def random_walk(
    count: int,
    *,
    seed: int = 7,
    start: float = 43_000.0,
    sigma_pct: float = 0.30,
    step_ms: int = STEP_MS,
) -> pd.DataFrame:
    """Örüntüsüz sentetik mum serisi."""
    generator = np.random.default_rng(seed)
    returns = generator.normal(0.0, sigma_pct / 100.0, count)
    close = start * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[start], close[:-1]])
    wick = np.abs(generator.normal(0.0, sigma_pct / 150.0, count))
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)
    volume = np.abs(generator.lognormal(3.0, 0.8, count))
    return pd.DataFrame(
        {
            "open_time": np.arange(count) * step_ms + 1_740_000_000_000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "quote_volume": volume * close,
            "trades": (volume * 10).astype("int64"),
            "taker_buy_base": volume * generator.uniform(0.35, 0.65, count),
            "taker_buy_quote": volume * close * 0.5,
            "is_closed": True,
        }
    )


def planted_signal(
    count: int = 12_000,
    *,
    seed: int = 11,
    drift_pct: float = 0.9,
    step_ms: int = STEP_MS,
) -> tuple[pd.DataFrame, np.ndarray]:
    """İçine gerçek bir kenar gömülmüş seri.

    Kural: hacim 20 mumluk ortalamanın iki katını aştığında sonraki üç mum
    yukarı yürür. Motor bu örüntüyü bulamazsa arama tarafında bir şey
    bozuktur.

    Döndürür: ``(mumlar, tetik_maskesi)``.
    """
    generator = np.random.default_rng(seed)
    base = generator.normal(0.0, 0.30 / 100.0, count)
    volume = np.abs(generator.lognormal(3.0, 0.8, count))

    average = pd.Series(volume).rolling(20, min_periods=20).mean().to_numpy()
    with np.errstate(invalid="ignore"):
        trigger = np.nan_to_num(volume / average) >= 2.0
    trigger[:20] = False

    # Tetik mumundan SONRAKİ üç muma yukarı itme eklenir; tetik mumunun
    # kendisine dokunulmaz, yoksa sinyal geçmişe sızardı.
    returns = base.copy()
    for offset in (1, 2, 3):
        shifted = np.zeros(count, dtype=bool)
        shifted[offset:] = trigger[: count - offset]
        returns[shifted] += drift_pct / 100.0 / 3.0

    close = 43_000.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[43_000.0], close[:-1]])
    wick = np.abs(generator.normal(0.0, 0.30 / 150.0, count))
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)

    frame = pd.DataFrame(
        {
            "open_time": np.arange(count) * step_ms + 1_740_000_000_000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "quote_volume": volume * close,
            "trades": (volume * 10).astype("int64"),
            "taker_buy_base": volume * generator.uniform(0.35, 0.65, count),
            "taker_buy_quote": volume * close * 0.5,
            "is_closed": True,
        }
    )
    return frame, trigger


def poison_future(frame: pd.DataFrame, cut: int, *, seed: int = 99) -> pd.DataFrame:
    """``cut`` satırından itibaren veriyi tanınmaz hale getirir.

    Look-ahead testinin aleti: geleceği bozup geçmişin değişmediğini
    doğrulamak, nedenselliği kanıtlamanın en doğrudan yoludur.
    """
    generator = np.random.default_rng(seed)
    poisoned = frame.copy()
    size = len(frame) - cut
    factor = generator.uniform(3.0, 9.0, size)
    for column in ("open", "high", "low", "close"):
        poisoned.loc[poisoned.index[cut:], column] = (
            frame[column].to_numpy()[cut:] * factor
        )
    for column in ("volume", "quote_volume", "taker_buy_base", "taker_buy_quote"):
        poisoned.loc[poisoned.index[cut:], column] = (
            frame[column].to_numpy()[cut:] * factor * 17.0
        )
    return poisoned
