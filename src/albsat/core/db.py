"""Uygulamanın SQLite dosyası (SPEC.md §3: emirler, işlemler, sinyaller, denetim).

Tek dosya: ``<veri dizini>/albsat.sqlite3``. Sinyal günlüğü (Faz 3), kâğıt
işlem kayıtları, risk durumu, ayarlar ve denetim kaydı (Faz 4) aynı dosyada
ayrı tablolarda durur. Yedeklemek ya da taşımak tek dosya kopyalamaktır.

Faz 4'te dosyaya aynı anda üç yerden yazılabilir: arayüz uçları, canlı veri
döngüsü ve Telegram komutları. Bu yüzden:

* **WAL kipi.** Okuyucular yazanı, yazan okuyucuları beklemez.
* **Meşgul bekleme süresi.** Kilit anlık doluysa hata vermek yerine birkaç
  saniye beklenir; "database is locked" kullanıcının göreceği bir hata
  olmamalı.
* **Her iş kendi bağlantısını açar.** SQLite bağlantısı iş parçacıkları
  arasında paylaşılmaz.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_FILENAME = "albsat.sqlite3"

#: Kilit doluysa en fazla bu kadar saniye beklenir.
BUSY_TIMEOUT_SECONDS = 10.0


def path_in(root: Path | str, filename: str = DEFAULT_FILENAME) -> Path:
    return Path(root) / filename


def connect(path: Path | str) -> sqlite3.Connection:
    """Satırları ada göre okunabilen, WAL kipinde bir bağlantı açar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def session(path: Path | str) -> Iterator[sqlite3.Connection]:
    """Tek bir iş için bağlantı: başarıda kaydeder, hatada geri alır, kapatır.

    ``with sqlite3.connect(...)`` bağlantıyı **kapatmaz**, yalnızca işlemi
    sonlandırır; uzun süre çalışan bir süreçte açık kalan bağlantılar
    birikir. Bu yardımcı ikisini birden yapar.
    """
    connection = connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def ensure_schema(path: Path | str, schema: str) -> None:
    with session(path) as connection:
        connection.executescript(schema)


__all__ = [
    "BUSY_TIMEOUT_SECONDS",
    "DEFAULT_FILENAME",
    "connect",
    "ensure_schema",
    "path_in",
    "session",
]
