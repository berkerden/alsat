"""Aynı veri dizininde tek kopya kilidi (Faz 7).

İki kopya aynı veri dizininde ve aynı canlı hesapta çalışırsa birbirinin
emirlerini "yetim" sayıp iptal eder (``docs/DEVIR-NOTU.md`` Faz 6 tuzak 5).
Mac'te bu, arayüzü iki Terminal penceresinde açmakla olur; sunucuda kendini
yeniden başlatan bir konteynerin yanında elle başlatılan ikinci bir kopyayla.
Eskiden arayüz dolu portu görünce bir sonrakini seçip ikinci kopyayı sessizce
açıyordu.

Kilit ``<veri>/.albsat-kilit`` dosyasında işletim sisteminin dosya kilididir
(``fcntl.flock``). Süreç ölünce kilit kendiliğinden kalkar; çöken bir kopya
kilidi "takılı" bırakamaz. Dosyada kilidi tutan sürecin kimliği ve başlangıç
zamanı yazar, yalnızca bilgi içindir.

Farklı bilgisayarlardaki kopyaları (Mac ve sunucu) bu kilit göremez; onlar
için geçiş rehberi Mac'teki canlı anahtarın Binance'te silinmesini ister.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import IO

from albsat.core.clock import iso, utc_now

FILENAME = ".albsat-kilit"


class AlreadyRunning(RuntimeError):
    """Bu veri dizininde başka bir kopya çalışıyor; mesaj kullanıcıya gösterilir."""


class InstanceLock:
    def __init__(self, root: Path | str, *, purpose: str = "arayüz") -> None:
        self.path = Path(root) / FILENAME
        self.purpose = purpose
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            holder = handle.read().strip() or "bilinmiyor"
            handle.close()
            raise AlreadyRunning(
                "Bu veri dizininde uygulama zaten çalışıyor "
                f"({holder}). İki kopya aynı hesapta birbirinin emirlerini iptal eder; "
                "ikinci kopya açılmadı."
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"süreç {os.getpid()}, {self.purpose}, başlangıç {iso(utc_now())} UTC\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        import fcntl

        with contextlib.suppress(OSError):
            self._handle.seek(0)
            self._handle.truncate()
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def held_by_other(root: Path | str) -> str | None:
    """Kilidi başka bir süreç tutuyorsa onun bilgisi; tutmuyorsa ``None``."""
    import fcntl

    path = Path(root) / FILENAME
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            return handle.read().strip() or "bilinmiyor"
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return None


__all__ = ["FILENAME", "AlreadyRunning", "InstanceLock", "held_by_other"]
