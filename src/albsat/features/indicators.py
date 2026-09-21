"""Teknik göstergeler (SPEC.md §4.2 "indikatör durumları").

**Bu modüldeki her fonksiyon nedenseldir (causal):** ``i`` satırındaki değer
yalnızca ``0..i`` satırlarına bakar. Geleceğe bakan tek bir işlem bile
(``shift(-1)``, ``center=True``, tüm seriyi tek seferde normalize etmek)
örüntü sonuçlarını olduğundan iyi gösterir ve bunu fark etmek neredeyse
imkânsızdır. ``tests/test_lookahead.py`` bunu mekanik olarak doğrular:
veri bir noktadan sonra bozulur, o noktadan önceki değerlerin değişmediği
kontrol edilir.

Isınma (warmup) satırları ``NaN``'dır ve bilerek doldurulmaz; bir örüntü
"ısınma bitmeden oluştu" diye sayılmamalıdır.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Üssel hareketli ortalama."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Wilder'ın gerçek aralığı: ``max(h-l, |h-önceki kapanış|, |l-önceki kapanış|)``."""
    high, low, close = frame["high"], frame["low"], frame["close"]
    previous_close = close.shift(1)
    spans = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    )
    return spans.max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder yumuşatmalı ATR."""
    return true_range(frame).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def atr_percent(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR'nin kapanış fiyatına oranı, yüzde olarak."""
    return atr(frame, period) / frame["close"] * 100.0


def rsi(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder'ın RSI'ı (0–100)."""
    change = frame["close"].diff()
    gain = change.clip(lower=0.0)
    loss = (-change).clip(lower=0.0)
    alpha = 1.0 / period
    average_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    # Kayıp sıfırsa RS sonsuzdur; RSI tanım gereği 100'dür.
    strength = average_gain / average_loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + strength)
    return result.where(average_loss != 0.0, 100.0).where(average_gain.notna())


def macd(
    frame: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD çizgisi, sinyal çizgisi ve histogram."""
    line = ema(frame["close"], fast) - ema(frame["close"], slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {"macd": line, "signal": signal_line, "histogram": line - signal_line}
    )


def bollinger(frame: pd.DataFrame, period: int = 20, deviations: float = 2.0) -> pd.DataFrame:
    """Bollinger bantları ve bant genişliği (band genişliği = (üst-alt)/orta)."""
    middle = sma(frame["close"], period)
    spread = frame["close"].rolling(period, min_periods=period).std(ddof=0) * deviations
    upper, lower = middle + spread, middle - spread
    width = (upper - lower) / middle.replace(0.0, np.nan) * 100.0
    return pd.DataFrame(
        {"middle": middle, "upper": upper, "lower": lower, "width_pct": width}
    )


def rolling_vwap(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    """Son ``period`` mumun hacim ağırlıklı ortalama fiyatı.

    Seans VWAP'ı yerine kayan pencere kullanılıyor: seans sınırı tanımı
    borsaya ve saat dilimine göre değişir, kayan pencere ise her periyotta
    aynı anlama gelir ve nedenselliği tartışmasızdır.
    """
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    volume = frame["volume"]
    weighted = (typical * volume).rolling(period, min_periods=period).sum()
    total = volume.rolling(period, min_periods=period).sum()
    return weighted / total.replace(0.0, np.nan)


def adx(frame: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder'ın ADX'i, +DI ve -DI ile birlikte. Trend/yatay ayrımı için."""
    high, low = frame["high"], frame["low"]
    up = high.diff()
    down = -low.diff()
    plus_move = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_move = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)

    alpha = 1.0 / period
    smoothed_range = true_range(frame).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = (
        plus_move.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
        / smoothed_range.replace(0.0, np.nan)
        * 100.0
    )
    minus_di = (
        minus_move.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
        / smoothed_range.replace(0.0, np.nan)
        * 100.0
    )
    total = (plus_di + minus_di).replace(0.0, np.nan)
    directional_index = (plus_di - minus_di).abs() / total * 100.0
    return pd.DataFrame(
        {
            "adx": directional_index.ewm(alpha=alpha, adjust=False, min_periods=period).mean(),
            "plus_di": plus_di,
            "minus_di": minus_di,
        }
    )


def rolling_percentile_rank(series: pd.Series, period: int) -> pd.Series:
    """Her değerin, kendisiyle biten ``period`` uzunluğundaki pencere içindeki
    yüzdelik sırası (0–1).

    "Bu ATR yüksek mi?" sorusunu yalnızca geçmişe bakarak cevaplar. Tüm
    serinin medyanıyla kıyaslamak geleceğe bakmak olurdu: 2026 Mart'ındaki
    bir mumun "sakin" sayılıp sayılmaması, Eylül'de ne olduğuna bağlı hale
    gelirdi.
    """
    return series.rolling(period, min_periods=period).rank(pct=True)
