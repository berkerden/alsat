"""Özellik ailelerini tek bir boole tabloya çevirir (SPEC.md §4.2).

Çıktı: her satır bir mum, her sütun "o mumda şu koşul sağlandı mı" sorusunun
cevabı. Örüntü arayıcı bu tabloyu tek başına ya da ikili kombinasyonlar
halinde tarar.

Üç kural:

1. **Yalnızca kapanmış mum.** Tablo ``closed_only`` süzgecinden geçmiş veri
   bekler; ``build_features`` bunu kendisi de uygular.
2. **Nedensellik.** ``i`` satırındaki hiçbir değer ``i``'den sonraki bir
   satıra bakmaz. Eşikler bile geçmişe göre belirlenir: "ATR yüksek mi"
   sorusu tüm serinin medyanıyla değil, son 200 mumun dağılımıyla
   cevaplanır.
3. **Isınma.** İlk ``warmup`` satır güvenilmezdir (EMA200, 200 mumluk
   yüzdelik pencereleri). ``FeatureSet.ready`` bu satırları işaretler ve
   örüntü arayıcı onları olay saymaz.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from albsat.data.klines import closed_only, interval_ms
from albsat.features import candles
from albsat.features.indicators import (
    adx,
    atr_percent,
    bollinger,
    ema,
    macd,
    rolling_percentile_rank,
    rolling_vwap,
    rsi,
    sma,
)

#: Uzun pencerelerin bittiği satır; öncesi örüntü sayılmaz.
WARMUP_BARS = 250
#: Yüzdelik sıralamalarda kullanılan geçmiş penceresi.
RANK_WINDOW = 200
#: İstanbul saati UTC+3'tür ve 2016'dan beri yaz saati uygulaması yoktur.
ISTANBUL_OFFSET_HOURS = 3

FAMILIES_TR = {
    "mum": "Mum formasyonu",
    "indikator": "İndikatör durumu",
    "hacim": "Hacim",
    "rejim": "Piyasa rejimi",
    "seviye": "Destek/direnç ve seviye",
    "zaman": "Zaman etkisi",
    "btc": "BTC etkisi",
}


@dataclass(frozen=True)
class FeatureSet:
    """Boole özellik tablosu ve açıklamaları."""

    frame: pd.DataFrame
    descriptions: Mapping[str, str] = field(default_factory=dict)
    families: Mapping[str, str] = field(default_factory=dict)
    warmup: int = WARMUP_BARS

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.frame.columns)

    @property
    def ready(self) -> pd.Series:
        """Isınma bitti mi? Örüntü arayıcı yalnızca ``True`` satırları olay sayar."""
        mask = pd.Series(False, index=self.frame.index)
        if len(mask) > self.warmup:
            mask.iloc[self.warmup :] = True
        return mask

    def describe(self, name: str) -> str:
        return self.descriptions.get(name, name)

    def family_of(self, name: str) -> str:
        return self.families.get(name, "?")


class _Builder:
    def __init__(self) -> None:
        self.columns: dict[str, pd.Series] = {}
        self.descriptions: dict[str, str] = {}
        self.families: dict[str, str] = {}

    def add(self, name: str, series: pd.Series, *, family: str, description: str) -> None:
        # NaN (ısınma, sıfıra bölme) "koşul sağlanmadı" demektir; örüntü
        # sayımına giren tek değer True'dur.
        self.columns[name] = series.fillna(False).astype(bool)
        self.descriptions[name] = description
        self.families[name] = family


def _crossed_above(series: pd.Series, other: pd.Series | float) -> pd.Series:
    """Bu mumda üstüne çıktı, önceki mumda altındaydı."""
    reference = other if isinstance(other, pd.Series) else pd.Series(other, index=series.index)
    return (series > reference) & (series.shift(1) <= reference.shift(1))


def _crossed_below(series: pd.Series, other: pd.Series | float) -> pd.Series:
    reference = other if isinstance(other, pd.Series) else pd.Series(other, index=series.index)
    return (series < reference) & (series.shift(1) >= reference.shift(1))


def _candle_features(frame: pd.DataFrame, builder: _Builder) -> None:
    add = builder.add
    add("mum_yutan_yukari", candles.bullish_engulfing(frame), family="mum",
        description="Yükseliş yutan mum")
    add("mum_yutan_asagi", candles.bearish_engulfing(frame), family="mum",
        description="Düşüş yutan mum")
    add("mum_cekic", candles.hammer(frame), family="mum",
        description="Çekiç (uzun alt fitil, küçük gövde)")
    add("mum_kayan_yildiz", candles.shooting_star(frame), family="mum",
        description="Kayan yıldız (uzun üst fitil, küçük gövde)")
    add("mum_doji", candles.doji(frame), family="mum",
        description="Doji (kararsız mum)")
    add("mum_ic_mum", candles.inside_bar(frame), family="mum",
        description="İç mum (önceki mumun aralığında)")
    add("mum_dis_mum", candles.outside_bar(frame), family="mum",
        description="Dış mum (önceki mumun aralığını aştı)")
    add("mum_igne_alt", candles.pin_bar_up(frame), family="mum",
        description="Alt fitili uzun iğne")
    add("mum_igne_ust", candles.pin_bar_down(frame), family="mum",
        description="Üst fitili uzun iğne")
    add("mum_uc_yukari", candles.three_up(frame), family="mum",
        description="Üst üste üç yükselen mum")
    add("mum_uc_asagi", candles.three_down(frame), family="mum",
        description="Üst üste üç düşen mum")


def _indicator_features(frame: pd.DataFrame, builder: _Builder) -> None:
    add = builder.add
    close = frame["close"]

    strength = rsi(frame, 14)
    add("rsi_asiri_satim", strength < 30, family="indikator", description="RSI 30'un altında")
    add("rsi_asiri_alim", strength > 70, family="indikator", description="RSI 70'in üstünde")
    add("rsi_orta_yukari", (strength >= 50) & (strength <= 70), family="indikator",
        description="RSI 50–70 bandında")
    add("rsi_30_yukari_kesti", _crossed_above(strength, 30.0), family="indikator",
        description="RSI 30'u yukarı kesti")
    add("rsi_70_asagi_kesti", _crossed_below(strength, 70.0), family="indikator",
        description="RSI 70'i aşağı kesti")

    lines = macd(frame)
    add("macd_yukari_kesisim", _crossed_above(lines["macd"], lines["signal"]),
        family="indikator", description="MACD sinyal çizgisini yukarı kesti")
    add("macd_asagi_kesisim", _crossed_below(lines["macd"], lines["signal"]),
        family="indikator", description="MACD sinyal çizgisini aşağı kesti")
    add("macd_pozitif", lines["histogram"] > 0, family="indikator",
        description="MACD histogramı pozitif")

    bands = bollinger(frame)
    add("boll_alt_bandin_altinda", close < bands["lower"], family="indikator",
        description="Kapanış alt Bollinger bandının altında")
    add("boll_ust_bandin_ustunde", close > bands["upper"], family="indikator",
        description="Kapanış üst Bollinger bandının üstünde")
    width_rank = rolling_percentile_rank(bands["width_pct"], RANK_WINDOW)
    add("boll_sikisma", width_rank < 0.20, family="indikator",
        description="Bollinger bantları son 200 muma göre dar (sıkışma)")
    add("boll_genisleme", width_rank > 0.80, family="indikator",
        description="Bollinger bantları son 200 muma göre geniş")

    ema20, ema50, ema200 = ema(close, 20), ema(close, 50), ema(close, 200)
    add("ema_yukari_dizilim", (ema20 > ema50) & (ema50 > ema200), family="indikator",
        description="EMA 20 > 50 > 200 (yükseliş dizilimi)")
    add("ema_asagi_dizilim", (ema20 < ema50) & (ema50 < ema200), family="indikator",
        description="EMA 20 < 50 < 200 (düşüş dizilimi)")
    add("ema_20_50_yukari_kesti", _crossed_above(ema20, ema50), family="indikator",
        description="EMA20, EMA50'yi yukarı kesti")
    add("ema_20_50_asagi_kesti", _crossed_below(ema20, ema50), family="indikator",
        description="EMA20, EMA50'yi aşağı kesti")

    vwap = rolling_vwap(frame, 20)
    add("vwap_ustunde", close > vwap, family="indikator",
        description="Fiyat 20 mumluk VWAP'ın üstünde")
    add("vwap_altinda", close < vwap, family="indikator",
        description="Fiyat 20 mumluk VWAP'ın altında")


def _volume_features(frame: pd.DataFrame, builder: _Builder) -> None:
    add = builder.add
    volume = frame["volume"]
    average = sma(volume, 20)
    ratio = volume / average.replace(0.0, np.nan)
    add("hacim_patlamasi", ratio >= 2.0, family="hacim",
        description="Hacim 20 mumluk ortalamanın en az 2 katı")
    add("hacim_kurumasi", ratio <= 0.5, family="hacim",
        description="Hacim 20 mumluk ortalamanın yarısından az")

    # Kline verisi taker alış hacmini ayrı taşır: agresif alıcı mı, agresif
    # satıcı mı baskın olduğunu emir defterine bakmadan söyler.
    taker_share = frame["taker_buy_base"] / volume.replace(0.0, np.nan)
    add("alici_baskin", taker_share > 0.60, family="hacim",
        description="Agresif alış hacmi toplam hacmin %60'ından fazla")
    add("satici_baskin", taker_share < 0.40, family="hacim",
        description="Agresif alış hacmi toplam hacmin %40'ından az")

    rising = frame["close"] > frame["open"]
    add("hacimsiz_yukselis", rising & (ratio < 1.0), family="hacim",
        description="Fiyat yükseldi ama hacim ortalamanın altında (uyumsuzluk)")


def _regime_features(frame: pd.DataFrame, builder: _Builder) -> None:
    add = builder.add
    directional = adx(frame, 14)
    strength = directional["adx"]
    add("rejim_trend", strength > 25, family="rejim", description="ADX 25'in üstünde (trend)")
    add("rejim_yatay", strength < 20, family="rejim", description="ADX 20'nin altında (yatay)")
    rising, falling = directional["plus_di"], directional["minus_di"]
    add("rejim_yukselen_trend", (strength > 25) & (rising > falling),
        family="rejim", description="Yükselen trend (ADX>25, +DI>-DI)")
    add("rejim_dusen_trend", (strength > 25) & (falling > rising),
        family="rejim", description="Düşen trend (ADX>25, -DI>+DI)")

    volatility = atr_percent(frame, 14)
    rank = rolling_percentile_rank(volatility, RANK_WINDOW)
    add("rejim_sakin", rank < 0.25, family="rejim",
        description="Oynaklık son 200 muma göre düşük (sakin piyasa)")
    add("rejim_oynak", rank > 0.75, family="rejim",
        description="Oynaklık son 200 muma göre yüksek (hareketli piyasa)")


def _level_features(frame: pd.DataFrame, builder: _Builder) -> None:
    add = builder.add
    close = frame["close"]
    # Kırılım için önceki mumlara bakılır; bu mumun kendi yükseği pencereye
    # girerse "her mum kendi zirvesini kırdı" gibi anlamsız bir sonuç çıkar.
    previous_high = frame["high"].shift(1).rolling(20, min_periods=20).max()
    previous_low = frame["low"].shift(1).rolling(20, min_periods=20).min()
    add("kirilim_yukari", close > previous_high, family="seviye",
        description="Kapanış son 20 mumun en yükseğini aştı")
    add("kirilim_asagi", close < previous_low, family="seviye",
        description="Kapanış son 20 mumun en düşüğünü kırdı")
    add("aralik_ustunde", close >= previous_high * 0.999, family="seviye",
        description="Kapanış 20 mumluk aralığın tepesine çok yakın")
    add("aralik_altinda", close <= previous_low * 1.001, family="seviye",
        description="Kapanış 20 mumluk aralığın dibine çok yakın")

    # Yuvarlak rakam: fiyatın kendi büyüklüğüne göre bir adım seçilir, böylece
    # aynı kural hem 43.000 USDT'lik BTC'de hem 180 USDT'lik SOL'da çalışır.
    step = np.power(10.0, np.floor(np.log10(close.replace(0.0, np.nan))) - 1.0)
    distance = (close % step) / step
    near_round = (distance < 0.05) | (distance > 0.95)
    add("yuvarlak_rakam_yakini", near_round, family="seviye",
        description="Fiyat yuvarlak bir rakamın %5'i kadar yakınında")


def _time_features(frame: pd.DataFrame, builder: _Builder) -> None:
    add = builder.add
    moment = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    hour = moment.dt.hour
    weekday = moment.dt.weekday
    istanbul_hour = (hour + ISTANBUL_OFFSET_HOURS) % 24

    add("seans_asya", (hour >= 0) & (hour < 8), family="zaman",
        description="Asya seansı (UTC 00–08)")
    add("seans_avrupa", (hour >= 7) & (hour < 15), family="zaman",
        description="Avrupa seansı (UTC 07–15)")
    add("seans_abd", (hour >= 13) & (hour < 21), family="zaman",
        description="ABD seansı (UTC 13–21)")
    add("hafta_sonu", weekday >= 5, family="zaman", description="Cumartesi veya pazar")
    add("hafta_basi", weekday == 0, family="zaman", description="Pazartesi")
    add("ist_mesai", (istanbul_hour >= 9) & (istanbul_hour < 18), family="zaman",
        description="İstanbul saatiyle 09:00–18:00")
    add("ist_gece", (istanbul_hour >= 0) & (istanbul_hour < 7), family="zaman",
        description="İstanbul saatiyle 00:00–07:00")


def _context_features(
    frame: pd.DataFrame, context: pd.DataFrame, builder: _Builder, *, interval: str
) -> None:
    """BTC'nin kısa vadeli yönü ve oynaklığı (SPEC.md §4.2 "BTC etkisi").

    Hizalama ``open_time`` üzerinden yapılır: aynı ``open_time``'a sahip BTC
    mumu, bu mumla **aynı anda** kapanır, yani bu mumun kapanışında zaten
    bilinir. Bir mum ileri kaydırmak gerekmez; bir mum geri kaydırmak ise
    bilgiyi boşuna eskitir.
    """
    context = closed_only(context)
    if context.empty:
        return

    reference = pd.DataFrame({"open_time": context["open_time"].to_numpy()})
    close = context["close"].reset_index(drop=True)
    reference["btc_getiri_3"] = close.pct_change(3) * 100.0
    reference["btc_oynaklik_sira"] = rolling_percentile_rank(
        atr_percent(context.reset_index(drop=True), 14), RANK_WINDOW
    )
    reference["btc_ema_dizilim"] = (
        ema(close, 20) > ema(close, 50)
    ) & (ema(close, 50) > ema(close, 200))

    merged = pd.merge(
        frame[["open_time"]], reference, on="open_time", how="left", validate="one_to_one"
    )
    add = builder.add
    add("btc_yukari", pd.Series(merged["btc_getiri_3"].to_numpy() > 0.0, index=frame.index),
        family="btc", description="BTC son 3 mumda yükselmiş")
    add("btc_asagi", pd.Series(merged["btc_getiri_3"].to_numpy() < 0.0, index=frame.index),
        family="btc", description="BTC son 3 mumda düşmüş")
    add("btc_oynak", pd.Series(merged["btc_oynaklik_sira"].to_numpy() > 0.75, index=frame.index),
        family="btc", description="BTC oynaklığı son 200 muma göre yüksek")
    add("btc_yukselis_dizilimi",
        pd.Series(merged["btc_ema_dizilim"].fillna(False).to_numpy(), index=frame.index),
        family="btc", description="BTC'de EMA 20 > 50 > 200")
    # ``interval`` yalnızca hizalamanın aynı periyotta yapıldığını belgelemek
    # için alınıyor; farklı periyot karıştırmak zaman damgası hizasını bozar.
    interval_ms(interval)


def build_features(
    frame: pd.DataFrame,
    *,
    interval: str,
    context: pd.DataFrame | None = None,
) -> FeatureSet:
    """Kapanmış mumlardan boole özellik tablosu üretir.

    ``context`` verilirse (genellikle aynı periyottaki BTCUSDT mumları) BTC
    etkisi ailesi de eklenir.
    """
    frame = closed_only(frame).reset_index(drop=True)
    builder = _Builder()
    if frame.empty:
        return FeatureSet(pd.DataFrame(index=frame.index), warmup=0)

    _candle_features(frame, builder)
    _indicator_features(frame, builder)
    _volume_features(frame, builder)
    _regime_features(frame, builder)
    _level_features(frame, builder)
    _time_features(frame, builder)
    if context is not None and not context.empty:
        _context_features(frame, context, builder, interval=interval)

    table = pd.DataFrame(builder.columns, index=frame.index)
    return FeatureSet(
        frame=table,
        descriptions=builder.descriptions,
        families=builder.families,
        warmup=min(WARMUP_BARS, len(frame)),
    )
