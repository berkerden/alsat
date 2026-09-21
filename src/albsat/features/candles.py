"""Mum formasyonları (SPEC.md §4.2 "mum formasyonları").

Her formasyon **boole** bir seri döndürür ve yalnızca o mumun kendisine ve
ondan öncekilere bakar.

Ölçüler mutlak fiyatla değil, mumun kendi aralığına oranla tanımlanır.
Aksi halde aynı kural BTCUSDT'de bir, SOLUSDT'de başka bir anlama gelirdi.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Gövdesi kendi aralığının bu oranından küçük olan mum "doji" sayılır.
DOJI_BODY_RATIO = 0.10
#: Bir fitilin "uzun" sayılması için aralığa oranı.
LONG_WICK_RATIO = 0.60
#: Çekiç/asılı adam gövdesinin aralığa azami oranı.
SMALL_BODY_RATIO = 0.35


def _parts(frame: pd.DataFrame) -> dict[str, pd.Series]:
    open_, high, low, close = frame["open"], frame["high"], frame["low"], frame["close"]
    span = (high - low).replace(0.0, np.nan)
    body = (close - open_).abs()
    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low
    return {
        "span": span,
        "body": body,
        "body_ratio": body / span,
        "upper_ratio": upper_wick / span,
        "lower_ratio": lower_wick / span,
        "bullish": close > open_,
        "bearish": close < open_,
    }


def doji(frame: pd.DataFrame) -> pd.Series:
    """Açılış ve kapanış neredeyse aynı: kararsızlık."""
    return _parts(frame)["body_ratio"] < DOJI_BODY_RATIO


def hammer(frame: pd.DataFrame) -> pd.Series:
    """Çekiç: küçük gövde yukarıda, uzun alt fitil. Satış baskısının emilmesi."""
    p = _parts(frame)
    return (p["body_ratio"] <= SMALL_BODY_RATIO) & (p["lower_ratio"] >= LONG_WICK_RATIO)


def shooting_star(frame: pd.DataFrame) -> pd.Series:
    """Kayan yıldız: küçük gövde aşağıda, uzun üst fitil. Alım baskısının emilmesi."""
    p = _parts(frame)
    return (p["body_ratio"] <= SMALL_BODY_RATIO) & (p["upper_ratio"] >= LONG_WICK_RATIO)


def bullish_engulfing(frame: pd.DataFrame) -> pd.Series:
    """Yükseliş yutan: düşen mumun gövdesini tamamen saran yükselen mum."""
    open_, close = frame["open"], frame["close"]
    previous_open, previous_close = open_.shift(1), close.shift(1)
    return (
        (close > open_)
        & (previous_close < previous_open)
        & (close >= previous_open)
        & (open_ <= previous_close)
    )


def bearish_engulfing(frame: pd.DataFrame) -> pd.Series:
    """Düşüş yutan: yükselen mumun gövdesini tamamen saran düşen mum."""
    open_, close = frame["open"], frame["close"]
    previous_open, previous_close = open_.shift(1), close.shift(1)
    return (
        (close < open_)
        & (previous_close > previous_open)
        & (close <= previous_open)
        & (open_ >= previous_close)
    )


def inside_bar(frame: pd.DataFrame) -> pd.Series:
    """İç mum: tamamı bir önceki mumun aralığında. Sıkışma."""
    return (frame["high"] <= frame["high"].shift(1)) & (frame["low"] >= frame["low"].shift(1))


def outside_bar(frame: pd.DataFrame) -> pd.Series:
    """Dış mum: bir önceki mumun aralığını aşan mum. Genişleme."""
    return (frame["high"] >= frame["high"].shift(1)) & (frame["low"] <= frame["low"].shift(1))


def pin_bar_up(frame: pd.DataFrame) -> pd.Series:
    """Alt fitili uzun iğne: fiyat aşağı sarkıp geri alınmış."""
    return _parts(frame)["lower_ratio"] >= LONG_WICK_RATIO


def pin_bar_down(frame: pd.DataFrame) -> pd.Series:
    """Üst fitili uzun iğne: fiyat yukarı çıkıp geri verilmiş."""
    return _parts(frame)["upper_ratio"] >= LONG_WICK_RATIO


def three_up(frame: pd.DataFrame) -> pd.Series:
    """Üst üste üç yükselen mum."""
    bullish = frame["close"] > frame["open"]
    return bullish & bullish.shift(1).fillna(False) & bullish.shift(2).fillna(False)


def three_down(frame: pd.DataFrame) -> pd.Series:
    """Üst üste üç düşen mum."""
    bearish = frame["close"] < frame["open"]
    return bearish & bearish.shift(1).fillna(False) & bearish.shift(2).fillna(False)
