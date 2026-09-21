"""Mum verisinin diskte saklanması (Parquet).

SPEC.md §3: mum verisi Parquet, işlem/sinyal/denetim kayıtları SQLite.
Bu modül yalnızca mum tarafını üstlenir ve ileride PostgreSQL'e geçişi
kolaylaştırmak için dosya yolu mantığını tek yerde toplar.

Düzen::

    <kök>/klines/<SEMBOL>/<periyot>.parquet
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from albsat.data.backfill import merge_frames


class KlineStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, symbol: str, interval: str) -> Path:
        return self.root / "klines" / symbol / f"{interval}.parquet"

    def exists(self, symbol: str, interval: str) -> bool:
        return self.path_for(symbol, interval).exists()

    def read(self, symbol: str, interval: str) -> pd.DataFrame:
        path = self.path_for(symbol, interval)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def write(self, frame: pd.DataFrame, *, symbol: str, interval: str) -> Path:
        path = self.path_for(symbol, interval)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Önce geçici dosyaya yaz, sonra yerine koy: yazma sırasında süreç
        # ölürse mevcut veri bozulmadan kalır.
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
        return path

    def upsert(self, frame: pd.DataFrame, *, symbol: str, interval: str) -> Path:
        """Yeni veriyi mevcutla birleştirip yazar (``open_time`` tekil)."""
        merged = merge_frames([self.read(symbol, interval), frame])
        return self.write(merged, symbol=symbol, interval=interval)

    def last_open_time(self, symbol: str, interval: str) -> int | None:
        frame = self.read(symbol, interval)
        if frame.empty:
            return None
        return int(frame["open_time"].max())
