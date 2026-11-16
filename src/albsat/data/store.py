"""Mum verisinin diskte saklanması (Parquet).

SPEC.md §3: mum verisi Parquet, işlem/sinyal/denetim kayıtları SQLite.
Bu modül yalnızca mum tarafını üstlenir ve ileride PostgreSQL'e geçişi
kolaylaştırmak için dosya yolu mantığını tek yerde toplar.

Düzen::

    <kök>/klines/<SEMBOL>/<periyot>.parquet
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import pandas as pd

from albsat.data.backfill import merge_frames
from albsat.data.klines import normalize_epoch_ms

#: Son okunan dosyalar bellekte tutulur. Arayüz beş saniyede bir, canlı döngü
#: her mumda aynı dosyayı baştan okuyordu; dosya ise en sık 15 dakikada bir
#: değişir. Anahtar dosyanın kimliğidir (inode, değişim zamanı, boyut):
#: ``write`` yeni dosyayı eskisinin yerine koyduğu için her yazımda değişir,
#: başka bir süreç (veri tazeleme) yazsa bile bayat kopya dönmez.
_CACHE_MAX_FILES = 8
_cache: OrderedDict[Path, tuple[tuple[int, int, int], pd.DataFrame]] = OrderedDict()
_cache_lock = threading.Lock()


def _signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def clear_cache() -> None:
    """Bellekteki kopyaları atar (testler ve dosyaya elle dokunan araçlar için)."""
    with _cache_lock:
        _cache.clear()


class KlineStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, symbol: str, interval: str) -> Path:
        return self.root / "klines" / symbol / f"{interval}.parquet"

    def exists(self, symbol: str, interval: str) -> bool:
        return self.path_for(symbol, interval).exists()

    def read(self, symbol: str, interval: str) -> pd.DataFrame:
        """Dosyadaki mumlar. Dönen tablo çağıranındır; değiştirmesi serbesttir."""
        return self._shared(symbol, interval).copy()

    def _shared(self, symbol: str, interval: str) -> pd.DataFrame:
        """Bellekteki ortak kopya. **Değiştirilmez**; dışarıya ``read`` kopyasını verir."""
        path = self.path_for(symbol, interval)
        signature = _signature(path)
        if signature is None:
            return pd.DataFrame()
        with _cache_lock:
            cached = _cache.get(path)
            if cached is not None and cached[0] == signature:
                _cache.move_to_end(path)
                return cached[1]
        frame = self._load(path)
        with _cache_lock:
            _cache[path] = (signature, frame)
            _cache.move_to_end(path)
            while len(_cache) > _CACHE_MAX_FILES:
                _cache.popitem(last=False)
        return frame

    @staticmethod
    def _load(path: Path) -> pd.DataFrame:
        frame = pd.read_parquet(path)
        if frame.empty or "open_time" not in frame.columns:
            return frame
        # Eski çalıştırmalarda arşiv zaman damgaları mikrosaniye olarak
        # kaydedilmiş olabilir. Okurken milisaniyeye çeviriyoruz; böylece
        # bozuk dosyayı silmek gerekmeden kendiliğinden onarılır.
        normalized = normalize_epoch_ms(frame["open_time"])
        if not normalized.equals(frame["open_time"]):
            frame = frame.copy()
            frame["open_time"] = normalized
            frame = frame.drop_duplicates(subset="open_time", keep="last")
            frame = frame.sort_values("open_time").reset_index(drop=True)
        return frame

    def write(self, frame: pd.DataFrame, *, symbol: str, interval: str) -> Path:
        path = self.path_for(symbol, interval)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Önce geçici dosyaya yaz, sonra yerine koy: yazma sırasında süreç
        # ölürse mevcut veri bozulmadan kalır.
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
        with _cache_lock:
            _cache.pop(path, None)
        return path

    def upsert(self, frame: pd.DataFrame, *, symbol: str, interval: str) -> Path:
        """Yeni veriyi mevcutla birleştirip yazar (``open_time`` tekil)."""
        merged = merge_frames([self.read(symbol, interval), frame])
        return self.write(merged, symbol=symbol, interval=interval)

    def last_open_time(self, symbol: str, interval: str) -> int | None:
        frame = self._shared(symbol, interval)
        if frame.empty:
            return None
        return int(frame["open_time"].max())
