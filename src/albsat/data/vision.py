"""Toplu geçmiş veri: ``data.binance.vision`` arşivleri.

İki yıllık 1 dakikalık veriyi REST ile çekmek yüz binlerce istek demektir.
Binance aynı veriyi aylık ve günlük ZIP arşivleri olarak yayımlıyor; bu
modül onları indirir, SHA256 sağlamasını doğrular ve ayrıştırır. REST
yalnızca arşivin bitiminden bugüne kalan boşluğu kapatmak için kullanılır
(bkz. ``albsat.data.backfill``).

Arşiv URL kalıbı::

    https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-08.zip
    https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.zip

Her arşivin yanında ``.CHECKSUM`` dosyası bulunur ve **her zaman** doğrulanır:
bozuk bir mum dosyası, sessizce yanlış bir backtest sonucu üretir.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Protocol, Sequence

import pandas as pd

from albsat.data.klines import KLINE_COLUMNS, parse_klines

BASE_URL = "https://data.binance.vision/data/spot"


class Downloader(Protocol):
    """İndirme arayüzü; test ederken sahte bir uygulamayla değiştirilir."""

    def get(self, url: str) -> bytes: ...


@dataclass(frozen=True)
class ArchiveRef:
    """Tek bir arşiv dosyasına işaret eder."""

    symbol: str
    interval: str
    period: str  # aylık için "2026-08", günlük için "2026-09-01"
    granularity: str  # "monthly" | "daily"

    @property
    def filename(self) -> str:
        return f"{self.symbol}-{self.interval}-{self.period}.zip"

    @property
    def url(self) -> str:
        return (
            f"{BASE_URL}/{self.granularity}/klines/"
            f"{self.symbol}/{self.interval}/{self.filename}"
        )

    @property
    def checksum_url(self) -> str:
        return f"{self.url}.CHECKSUM"


def month_range(start: date, end: date) -> Iterator[str]:
    """``start`` ve ``end`` arasındaki ayları ``YYYY-MM`` olarak üretir."""
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month > 12:
            month, year = 1, year + 1


def day_range(start: date, end: date) -> Iterator[str]:
    """``start`` ve ``end`` arasındaki günleri ``YYYY-MM-DD`` olarak üretir."""
    current = start
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def plan_archives(
    symbol: str, interval: str, start: date, end: date
) -> list[ArchiveRef]:
    """İndirme planı: tam aylar aylık arşivden, son kısmi ay günlükten.

    Binance aylık arşivi ancak ay bittikten sonra yayımlar; içinde
    bulunulan ay için günlük dosyalara düşülür.
    """
    refs: list[ArchiveRef] = []
    last_full_month_end = end.replace(day=1) - timedelta(days=1)

    if start <= last_full_month_end:
        for period in month_range(start, last_full_month_end):
            refs.append(ArchiveRef(symbol, interval, period, "monthly"))

    first_daily = max(start, end.replace(day=1))
    if first_daily <= end:
        for period in day_range(first_daily, end):
            refs.append(ArchiveRef(symbol, interval, period, "daily"))

    return refs


def verify_checksum(payload: bytes, checksum_text: str, *, filename: str) -> None:
    """``.CHECKSUM`` dosyasına karşı SHA256 doğrulaması yapar."""
    expected = checksum_text.strip().split()[0].lower()
    actual = hashlib.sha256(payload).hexdigest().lower()
    if actual != expected:
        raise ValueError(
            f"{filename} sağlama hatası: beklenen {expected}, bulunan {actual}. "
            "Dosya bozuk indirilmiş olabilir; tekrar deneyin."
        )


def _looks_like_header(row: Sequence[str]) -> bool:
    """Arşiv CSV'leri 2025 sonrası başlık satırı içeriyor; eskiler içermiyor."""
    if not row:
        return True
    try:
        int(row[0])
    except (TypeError, ValueError):
        return True
    return False


def read_archive(payload: bytes, *, interval: str) -> pd.DataFrame:
    """ZIP içeriğini ``DataFrame``'e çevirir.

    Arşiv verisi tanım gereği kapanmış mumlardan oluşur, bu yüzden
    ``is_closed`` her satırda ``True``'dur.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError("Arşivin içinde CSV dosyası yok.")
        with archive.open(names[0]) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8")
            rows = list(csv.reader(text))

    if rows and _looks_like_header(rows[0]):
        rows = rows[1:]

    width = len(KLINE_COLUMNS)
    rows = [r[:width] + [""] * (width - len(r)) for r in rows if r]
    return parse_klines(rows, interval=interval)


def fetch_archive(
    ref: ArchiveRef,
    downloader: Downloader,
    *,
    cache_dir: Path | None = None,
    verify: bool = True,
) -> pd.DataFrame:
    """Bir arşivi indirir (varsa önbellekten okur), doğrular ve ayrıştırır."""
    cached: Path | None = None
    if cache_dir is not None:
        cached = cache_dir / ref.symbol / ref.interval / ref.filename
        if cached.exists():
            return read_archive(cached.read_bytes(), interval=ref.interval)

    payload = downloader.get(ref.url)
    if verify:
        checksum = downloader.get(ref.checksum_url).decode("utf-8", "replace")
        verify_checksum(payload, checksum, filename=ref.filename)

    if cached is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(payload)

    return read_archive(payload, interval=ref.interval)
