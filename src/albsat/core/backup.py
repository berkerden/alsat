"""Veritabanının ve küçük ayar dosyalarının günlük yedeği (SPEC.md §7).

**Ne yedeklenir.** ``albsat.sqlite3`` (emirler, işlemler, sinyaller, risk
durumu, ayarlar, denetim kaydı) ve veri dizininin kökündeki ``.json``
dosyaları (kural deposu, komisyon, borsa filtreleri, Telegram sohbet
kimliği). Mum verisi yedeklenmez: büyüktür ve borsadan yeniden indirilir.
**Sır yedeklenmez**; sırlar veri dizininde durmaz.

**Nasıl.** SQLite'ın çevrimiçi yedek API'si (``Connection.backup``)
uygulama çalışırken tutarlı bir kopya alır. Dosyayı düz kopyalamak WAL
kipinde yarım yazılmış bir sayfayı da kopyalayabilir; o kopya ancak geri
yüklenince bozuk çıkar. Kopya ``PRAGMA integrity_check`` ile sınanır, her
dosyanın SHA-256 özeti ``YEDEK-BILGI.json``'a yazılır ve hepsi tek bir
``.tar.gz`` arşivine konur. Arşiv önce geçici adla yazılır, sonra yeniden
adlandırılır: yarım arşiv yedek sayılmaz.

**Nereye.** ``<veri>/yedek/albsat-yedek-YYYYMMDD-HHMMSS.tar.gz`` (UTC). Son
:data:`KEEP` yedek tutulur. Yedek aynı diskte durduğu için diskin kendisi
giderse yedek de gider; sunucudaki yedeği ara ara Mac'e indirmek geçiş
rehberinde anlatılıyor (``docs/FAZ7-SUNUCU.md``).

**Geri yükleme** yalnızca uygulama kapalıyken yapılır. Mevcut dosyalar
silinmez, ``yedek/geri-yukleme-oncesi-<zaman>/`` altına taşınır.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from albsat.core import db
from albsat.core.clock import as_utc, iso, utc_now

logger = logging.getLogger(__name__)

DIRECTORY = "yedek"
PREFIX = "albsat-yedek-"
SUFFIX = ".tar.gz"
MANIFEST = "YEDEK-BILGI.json"
#: Tutulan en fazla yedek sayısı (günde bir yedekle iki hafta).
KEEP = 14
#: Son yedek bundan eskiyse yenisi alınır.
MAX_AGE = timedelta(hours=24)
#: Zamanlayıcı bu sıklıkla bakar.
CHECK_SECONDS = 3600.0

_NAME = re.compile(rf"^{re.escape(PREFIX)}(\d{{8}}-\d{{6}}){re.escape(SUFFIX)}$")
#: Arşive girebilecek ayar dosyaları: veri dizininin kökündeki düz ``.json``.
_JSON = re.compile(r"^[A-Za-z0-9._-]+\.json$")


class BackupError(RuntimeError):
    """Yedek alınamadı ya da geri yüklenemedi; mesaj kullanıcıya gösterilir."""


@dataclass(frozen=True)
class BackupResult:
    yol: Path
    boyut_bayt: int
    dosyalar: tuple[str, ...]
    zaman_utc: str


@dataclass(frozen=True)
class RestoreResult:
    arsiv: Path
    dosyalar: tuple[str, ...]
    eskiler: Path


def directory(root: Path | str) -> Path:
    return Path(root) / DIRECTORY


def backup_time(path: Path) -> datetime | None:
    match = _NAME.match(path.name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def list_backups(root: Path | str) -> list[Path]:
    """Yedekler, en yenisi başta."""
    folder = directory(root)
    if not folder.is_dir():
        return []
    found = [item for item in folder.iterdir() if item.is_file() and backup_time(item) is not None]
    return sorted(found, key=lambda item: item.name, reverse=True)


def latest_age(root: Path | str, now: datetime | None = None) -> timedelta | None:
    backups = list_backups(root)
    if not backups:
        return None
    moment = backup_time(backups[0])
    assert moment is not None
    return (now or utc_now()) - moment


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _integrity(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    finally:
        connection.close()
    if [tuple(row) for row in rows] != [("ok",)]:
        detail = "; ".join(str(row[0]) for row in rows[:3])
        raise BackupError(f"Veritabanı kopyası bütünlük sınamasını geçmedi: {detail}")


def _copy_database(source: Path, target: Path) -> None:
    """Çevrimiçi yedek: uygulama yazarken de tutarlı kopya."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=db.BUSY_TIMEOUT_SECONDS)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _json_files(root: Path) -> list[Path]:
    return sorted(
        item for item in root.iterdir()
        if item.is_file() and not item.is_symlink() and _JSON.match(item.name)
    )


def create(
    root: Path | str,
    *,
    now: datetime | None = None,
    keep: int = KEEP,
    version: str = "",
) -> BackupResult:
    """Yedek alır, sınar, eskileri siler."""
    root = Path(root)
    moment = now or utc_now()
    database = db.path_in(root)
    if not database.exists():
        raise BackupError(f"Veritabanı bulunamadı: {database}. Uygulama bu veri "
                          "dizininde hiç açılmamış; yedeklenecek kayıt yok.")
    folder = directory(root)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{PREFIX}{as_utc(moment).strftime('%Y%m%d-%H%M%S')}{SUFFIX}"
    final = folder / name

    with tempfile.TemporaryDirectory(dir=folder, prefix=".hazirlik-") as work:
        staging = Path(work)
        copy = staging / db.DEFAULT_FILENAME
        try:
            _copy_database(database, copy)
        except sqlite3.Error as error:
            raise BackupError(f"Veritabanı kopyalanamadı: {error}") from None
        _integrity(copy)
        members: list[tuple[str, Path]] = [(db.DEFAULT_FILENAME, copy)]
        for item in _json_files(root):
            members.append((item.name, item))
        manifest = {
            "zaman_utc": iso(moment),
            "surum": version,
            "dosyalar": {arcname: _sha256(path) for arcname, path in members},
        }
        partial = staging / (name + ".yarim")
        with tarfile.open(partial, "w:gz") as archive:
            data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(data)
            info.mtime = int(moment.timestamp())
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
            for arcname, path in members:
                archive.add(path, arcname=arcname, recursive=False)
        os.chmod(partial, 0o600)
        os.replace(partial, final)

    for old in list_backups(root)[max(keep, 1):]:
        with contextlib.suppress(OSError):
            old.unlink()
    return BackupResult(
        yol=final, boyut_bayt=final.stat().st_size,
        dosyalar=tuple(arcname for arcname, _ in members), zaman_utc=iso(moment),
    )


def verify(archive_path: Path | str) -> dict[str, Any]:
    """Arşivi açmadan önce sınar: yalnızca beklenen adlar, özetler tutuyor."""
    archive_path = Path(archive_path)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if MANIFEST not in names:
                raise BackupError("Arşivde YEDEK-BILGI.json yok; bu bir albsat yedeği değil.")
            for member in members:
                if not member.isfile() or "/" in member.name or member.name.startswith("."):
                    raise BackupError(f"Arşivde beklenmeyen öğe: {member.name!r}")
                if member.name not in (MANIFEST, db.DEFAULT_FILENAME) and not _JSON.match(
                    member.name
                ):
                    raise BackupError(f"Arşivde beklenmeyen dosya: {member.name!r}")
            handle = archive.extractfile(MANIFEST)
            assert handle is not None
            manifest = json.loads(handle.read().decode("utf-8"))
            expected: dict[str, str] = manifest.get("dosyalar", {})
            if db.DEFAULT_FILENAME not in expected:
                raise BackupError("Arşivde veritabanı yok.")
            for arcname, digest in expected.items():
                member_handle = archive.extractfile(arcname)
                if member_handle is None:
                    raise BackupError(f"Arşivde {arcname} eksik.")
                actual = hashlib.sha256(member_handle.read()).hexdigest()
                if actual != digest:
                    raise BackupError(f"{arcname} özeti tutmuyor; arşiv bozuk.")
    except (tarfile.TarError, OSError, ValueError) as error:
        raise BackupError(f"Arşiv okunamadı: {error}") from None
    return dict(manifest)


def app_running(port: int, host: str = "127.0.0.1") -> bool:
    """Bu bilgisayarda arayüz sunucusu dinliyor mu?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


def restore(
    root: Path | str,
    archive_path: Path | str,
    *,
    now: datetime | None = None,
    running: Callable[[], bool] = lambda: False,
) -> RestoreResult:
    """Yedeği geri yükler. Uygulama çalışıyorsa reddeder."""
    root = Path(root)
    archive_path = Path(archive_path)
    if running():
        raise BackupError(
            "Uygulama çalışıyor görünüyor. Önce kapatın, sonra geri yükleyin; açıkken "
            "dosyanın altı değişirse kayıtlar bozulur."
        )
    manifest = verify(archive_path)
    moment = now or utc_now()
    aside = directory(root) / f"geri-yukleme-oncesi-{as_utc(moment).strftime('%Y%m%d-%H%M%S')}"
    aside.mkdir(parents=True, exist_ok=False)
    restored: list[str] = []
    with tempfile.TemporaryDirectory(dir=directory(root), prefix=".geri-") as work:
        staging = Path(work)
        with tarfile.open(archive_path, "r:gz") as archive:
            for arcname in manifest["dosyalar"]:
                handle = archive.extractfile(arcname)
                assert handle is not None
                target = staging / arcname
                with target.open("wb") as out:
                    shutil.copyfileobj(handle, out)
        _integrity(staging / db.DEFAULT_FILENAME)
        database = db.path_in(root)
        for suffix in ("", "-wal", "-shm"):
            current = database.with_name(database.name + suffix)
            if current.exists():
                os.replace(current, aside / current.name)
        for item in _json_files(root):
            os.replace(item, aside / item.name)
        for arcname in manifest["dosyalar"]:
            os.replace(staging / arcname, root / arcname)
            restored.append(arcname)
    return RestoreResult(arsiv=archive_path, dosyalar=tuple(restored), eskiler=aside)


class BackupScheduler:
    """Uygulama açıkken günde bir yedek: açılışta ve saatte bir bakar."""

    def __init__(
        self,
        root: Path | str,
        *,
        on_failure: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] = utc_now,
        interval: float = CHECK_SECONDS,
        version: str = "",
    ) -> None:
        self.root = Path(root)
        self.on_failure = on_failure
        self.clock = clock
        self.interval = interval
        self.version = version
        self.son_yedek: BackupResult | None = None
        self.son_hata: str | None = None
        self.son_hata_utc: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure_reported = False

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="yedek", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(30)

    def _run(self) -> None:
        # Açılışta hemen değil: canlı döngü önce uzlaştırsın, disk sakinleşsin.
        if self._stop.wait(60.0):
            return
        while True:
            self.run_once()
            if self._stop.wait(self.interval):
                return

    def run_once(self) -> BackupResult | None:
        """Son yedek eskiyse yenisini alır; almadıysa ``None``."""
        now = self.clock()
        age = latest_age(self.root, now)
        if age is not None and age < MAX_AGE:
            return None
        if not db.path_in(self.root).exists():
            return None
        started = time.monotonic()
        try:
            result = create(self.root, now=now, version=self.version)
        except (BackupError, OSError) as error:
            self.son_hata = str(error)[:300]
            self.son_hata_utc = iso(now)
            logger.warning("Yedek alınamadı: %s", error)
            if not self._failure_reported and self.on_failure is not None:
                self._failure_reported = True
                self.on_failure(f"💾 Günlük yedek alınamadı: {self.son_hata}")
            return None
        self.son_yedek = result
        self.son_hata = None
        self._failure_reported = False
        logger.info("Yedek alındı: %s (%.1f sn)", result.yol.name, time.monotonic() - started)
        return result

    def status(self) -> dict[str, Any]:
        backups = list_backups(self.root)
        newest = backups[0] if backups else None
        moment = backup_time(newest) if newest is not None else None
        return {
            "son_yedek_utc": iso(moment) if moment is not None else None,
            "son_yedek_dosya": newest.name if newest is not None else None,
            "yedek_sayisi": len(backups),
            "yedek_dizini": str(directory(self.root).resolve()),
            "son_hata": self.son_hata,
            "son_hata_utc": self.son_hata_utc,
        }


__all__ = [
    "KEEP",
    "MAX_AGE",
    "BackupError",
    "BackupResult",
    "BackupScheduler",
    "RestoreResult",
    "app_running",
    "backup_time",
    "create",
    "directory",
    "latest_age",
    "list_backups",
    "restore",
    "verify",
]
