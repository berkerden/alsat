"""Mum (kline) verisinin şeması, ayrıştırılması ve kalite kontrolü.

Binance'in kline temsili hem REST (``GET /api/v3/klines``) hem toplu arşiv
(``data.binance.vision`` CSV) tarafında aynı 12 alanlı dizidir. Tek bir
ayrıştırıcı ikisini de karşılar.

**Kritik kural (SPEC.md §11):** Tahmin ve sinyal üretiminde asla kapanmamış
mum kullanılmaz. Bu modül her mum için ``is_closed`` bilgisini taşır;
özellik hesaplayan hiçbir kod ``is_closed=False`` olan satırı görmez.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

#: Binance kline dizisindeki alan sırası (REST ve arşiv CSV ortak).
KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)

#: Saklanan kolonlar ve tipleri.
STORED_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
)

#: Desteklenen periyotlar ve milisaniye karşılıkları.
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def interval_ms(interval: str) -> int:
    try:
        return INTERVAL_MS[interval]
    except KeyError:
        raise ValueError(
            f"Desteklenmeyen periyot: {interval!r}. "
            f"Geçerli değerler: {', '.join(INTERVAL_MS)}"
        ) from None


def candles_per_day(interval: str) -> float:
    return 86_400_000 / interval_ms(interval)


# --- Zaman damgası birimi -------------------------------------------------
#
# Binance **1 Ocak 2025'ten itibaren** toplu arşivlerde (data.binance.vision)
# zaman damgalarını **mikrosaniye** cinsinden yayımlıyor; REST uç noktası
# ``GET /api/v3/klines`` ise hâlâ **milisaniye** döndürüyor.
# Kaynak: binance/binance-public-data README — "The timestamp for SPOT Data
# from January 1st 2025 onwards will be in microseconds."
#
# İki kaynağı aynı tabloda birleştiren her kod bunu düzeltmek zorundadır.
# Düzeltilmezse ardışık iki 1m mumu arasındaki fark 60.000.000 olur, beklenen
# 60.000 ile karşılaştırılır ve **neredeyse her mum çifti "boşluk" sanılır**.
# Bu da on binlerce gereksiz REST isteği ve IP yasağı riski demektir.
#
# Birime "varsayım" yerine "ölçüm" ile karar veriyoruz: makul bir tarih
# aralığı (1973 → 5138) her birimde ayrık bir büyüklük bandına düşer, bu
# yüzden değerin kendisi birimini ele verir. Karar satır satır verilir; eski
# çalıştırmalardan kalmış, mikrosaniye ve milisaniye satırları karışmış bir
# dosya da böylece kendiliğinden onarılır.
_SECONDS_RANGE = (10**8, 10**11)
_MILLISECONDS_RANGE = (10**11, 10**14)
_MICROSECONDS_RANGE = (10**14, 10**17)
_NANOSECONDS_RANGE = (10**17, 10**20)


def normalize_epoch_ms(values) -> pd.Series:
    """Hangi birimde gelirse gelsin zaman damgalarını milisaniyeye çevirir.

    Saniye, milisaniye, mikrosaniye ve nanosaniye tanınır. Büyüklüğü
    tanınan bantların dışında kalan değerler (0, test verisi, bozuk satır)
    olduğu gibi bırakılır — sessizce yanlış bir tarihe kaydırmaktansa
    dokunmamak daha güvenlidir.
    """
    series = pd.Series(values)
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    array = numeric.to_numpy(dtype="int64")
    magnitude = np.abs(array)
    out = array.copy()

    # Her dönüşüm yalnızca kendi bandındaki satırlara uygulanır; tüm diziyi
    # çarpıp maskelemek nanosaniye değerlerinde int64 taşması üretirdi.
    seconds = (magnitude >= _SECONDS_RANGE[0]) & (magnitude < _SECONDS_RANGE[1])
    out[seconds] = array[seconds] * 1_000

    micro = (magnitude >= _MICROSECONDS_RANGE[0]) & (magnitude < _MICROSECONDS_RANGE[1])
    out[micro] = array[micro] // 1_000

    nano = (magnitude >= _NANOSECONDS_RANGE[0]) & (magnitude < _NANOSECONDS_RANGE[1])
    out[nano] = array[nano] // 1_000_000

    return pd.Series(out, index=series.index, dtype="int64")


def parse_klines(rows, *, interval: str, now_ms: int | None = None) -> pd.DataFrame:
    """Ham kline dizilerini ``DataFrame``'e çevirir.

    ``now_ms`` verilirse, kapanış zamanı henüz gelmemiş son mum
    ``is_closed=False`` olarak işaretlenir. Verilmezse tüm mumlar kapanmış
    sayılır (arşiv verisi için doğru varsayım).
    """
    frame = pd.DataFrame(list(rows), columns=list(KLINE_COLUMNS))
    if frame.empty:
        empty = pd.DataFrame(columns=list(STORED_COLUMNS) + ["is_closed"])
        return empty

    numeric = ("open", "high", "low", "close", "volume", "quote_volume",
               "taker_buy_base", "taker_buy_quote")
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["trades"] = pd.to_numeric(frame["trades"], errors="coerce").fillna(0).astype("int64")
    # Arşiv mikrosaniye, REST milisaniye döndürür; ikisi aynı tabloda
    # buluşmadan önce tek birime indirilir.
    for column in ("open_time", "close_time"):
        frame[column] = normalize_epoch_ms(frame[column])

    step = interval_ms(interval)
    if now_ms is None:
        frame["is_closed"] = True
    else:
        frame["is_closed"] = (frame["open_time"] + step) <= now_ms

    frame = frame[list(STORED_COLUMNS) + ["is_closed"]]
    # Birimi çevrildikten sonra hâlâ makul bir tarihe düşmeyen satır bozuktur
    # (kısa kesilmiş CSV satırı, boş alan). Tabloda bırakılırsa ``find_gaps``
    # onunla ilk gerçek mum arasını yıllarca süren tek bir boşluk sanar ve
    # onarım binlerce istek yapar. Sayıyı uydurmaktansa satırı atıyoruz;
    # kalite raporu eksiği zaten bildirir.
    plausible = frame["open_time"].between(
        _MILLISECONDS_RANGE[0], _MILLISECONDS_RANGE[1] - 1
    )
    frame = frame.loc[plausible]
    return frame.sort_values("open_time").reset_index(drop=True)


def closed_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Yalnızca kapanmış mumlar. Özellik hesabına giden tek kapı burasıdır."""
    if "is_closed" not in frame.columns:
        return frame
    return frame.loc[frame["is_closed"]].reset_index(drop=True)


def to_utc(open_time_ms: int) -> datetime:
    return datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)


@dataclass(frozen=True)
class QualityReport:
    """SPEC.md §4.1'in istediği veri kalite raporu."""

    symbol: str
    interval: str
    rows: int
    first_open_time: int | None
    last_open_time: int | None
    missing_candles: int
    duplicate_rows: int
    zero_volume_rows: int
    gaps: tuple[tuple[int, int], ...]
    invalid_ohlc_rows: int

    @property
    def expected_rows(self) -> int:
        if self.first_open_time is None or self.last_open_time is None:
            return 0
        step = interval_ms(self.interval)
        return int((self.last_open_time - self.first_open_time) // step) + 1

    @property
    def completeness_pct(self) -> float:
        expected = self.expected_rows
        return 100.0 if expected == 0 else round(self.rows / expected * 100, 4)

    @property
    def usable(self) -> bool:
        """Araştırmaya girmeye uygun mu?

        Eşikler temkinli: %99,5 eksiksizlik ve geçersiz OHLC satırı olmaması.
        """
        return (
            self.completeness_pct >= 99.5
            and self.invalid_ohlc_rows == 0
            and self.duplicate_rows == 0
        )

    def summary_tr(self) -> str:
        if self.rows == 0:
            return f"{self.symbol} {self.interval}: veri yok."
        first = to_utc(self.first_open_time or 0).date()
        last = to_utc(self.last_open_time or 0).date()
        verdict = "kullanılabilir" if self.usable else "SORUNLU"
        return (
            f"{self.symbol} {self.interval}: {self.rows:,} mum "
            f"({first} → {last}), eksiksizlik %{self.completeness_pct:.2f}, "
            f"eksik {self.missing_candles:,}, tekrar {self.duplicate_rows:,}, "
            f"sıfır hacim {self.zero_volume_rows:,}, boşluk {len(self.gaps)} — {verdict}"
        )


def check_quality(frame: pd.DataFrame, *, symbol: str, interval: str) -> QualityReport:
    """Eksik mum, tekrar kayıt, zaman boşluğu ve sıfır hacim tespiti."""
    if frame.empty:
        return QualityReport(symbol, interval, 0, None, None, 0, 0, 0, (), 0)

    frame = frame.sort_values("open_time").reset_index(drop=True)
    step = interval_ms(interval)
    times = frame["open_time"].to_numpy()

    duplicates = int(frame["open_time"].duplicated().sum())

    gaps: list[tuple[int, int]] = []
    missing = 0
    deltas = times[1:] - times[:-1]
    for index, delta in enumerate(deltas):
        if delta > step:
            skipped = int(delta // step) - 1
            missing += skipped
            gaps.append((int(times[index]) + step, int(times[index + 1]) - step))

    zero_volume = int((frame["volume"] <= 0).sum())

    invalid = int(
        (
            (frame["high"] < frame["low"])
            | (frame["high"] < frame["open"])
            | (frame["high"] < frame["close"])
            | (frame["low"] > frame["open"])
            | (frame["low"] > frame["close"])
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        ).sum()
    )

    return QualityReport(
        symbol=symbol,
        interval=interval,
        rows=int(len(frame)),
        first_open_time=int(times[0]),
        last_open_time=int(times[-1]),
        missing_candles=missing,
        duplicate_rows=duplicates,
        zero_volume_rows=zero_volume,
        gaps=tuple(gaps),
        invalid_ohlc_rows=invalid,
    )
