"""Sır deposu: macOS Anahtar Zinciri (Keychain) ya da sunucuda korumalı dizin.

**Mac'te** sırlar Anahtar Zinciri'nde durur. Sır **komut satırı argümanı
olarak geçmez**. ``security add-generic-password -w <sır>`` sırrı süreç
listesinde (``ps``) görünür kılar; bunun yerine ``security -i`` (etkileşimli
kip) çalıştırılır ve komut **standart girdiden** verilir. Okurken sır
yalnızca komutun çıktısıdır.

**Sunucuda** (Faz 7) Anahtar Zinciri yoktur. Sırlar
``ALBSAT_SIR_DIZINI`` ortam değişkeninin gösterdiği dizinde, her kayıt ayrı
bir dosyada durur: ``<dizin>/<servis>/<hesap>``. Ortam değişkeni yalnızca
**yeri** söyler, sırrın kendisini taşımaz (proje kuralı: ortam değişkenine sır
konmaz). Dizin ve dosyalar yalnızca sahibince okunabilmelidir (dizin 700,
dosya 600) ve uygulamanın kullanıcısına ait olmalıdır; değilse okunmaz ve
nedeni söylenir, tıpkı ``ssh``'in açık izinli anahtarı reddetmesi gibi.
Dosya önce geçici adla yazılır, sonra yeniden adlandırılır; yarım dosya kalmaz.

Sırrın içinde yalnızca güvenli karakterler olabilir (harf, rakam ve
``: _ - + / = . ~``; son ikisi Faz 7'de gözcü adresi için eklendi); başka
karakter içeren bir değer yazılmaz. Böylece komut satırının tırnaklanmasıyla
oynanamaz.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from albsat.exchange.keys import SecretText

#: Sunucuda sır dizininin yerini söyleyen ortam değişkeni (sırrın kendisi değil).
DIRECTORY_ENV = "ALBSAT_SIR_DIZINI"

_SAFE = re.compile(r"^[A-Za-z0-9:_\-+/=.~]{8,4096}$")
_NAME = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")

_WHERE_MAC = {"e": "Mac'in Anahtar Zinciri'ne", "de": "Mac'in Anahtar Zinciri'nde",
              "den": "Mac'in Anahtar Zinciri'nden"}
_WHERE_DIR = {"e": "sunucunun sır dizinine", "de": "sunucunun sır dizininde",
              "den": "sunucunun sır dizininden"}


class KeychainError(RuntimeError):
    """Sır deposu işlemi başarısız; mesaj kullanıcıya gösterilir, sır içermez."""


def secret_directory() -> Path | None:
    """Sunucudaki sır dizini; ortam değişkeni yoksa ``None`` (Mac'te Anahtar Zinciri)."""
    value = os.environ.get(DIRECTORY_ENV, "").strip()
    return Path(value) if value else None


def available() -> bool:
    return secret_directory() is not None or sys.platform == "darwin"


def where(case: str = "de") -> str:
    """Kullanıcıya gösterilecek yer adı: ``"e"`` (-e), ``"de"`` (-de), ``"den"`` (-den)."""
    table = _WHERE_DIR if secret_directory() is not None else _WHERE_MAC
    return table[case]


def store_name() -> str:
    """Faz 7'den önceki metinlerle aynı kalıp: "... kaydedildi"."""
    return where("e")


def unavailable_reason() -> str:
    return ("Sırlar Mac'te Anahtar Zinciri'nde, sunucuda ALBSAT_SIR_DIZINI ortam "
            "değişkeninin gösterdiği dizinde saklanır. Bu bilgisayarda ikisi de yok; "
            "bu komut yalnızca Mac'te ya da sunucu kurulumundaki konteynerde çalışır.")


def _check_names(service: str, account: str) -> None:
    for name in (service, account):
        if not _NAME.match(name) or name in (".", ".."):
            raise KeychainError("Sır kaydının adı geçersiz.")


# --- sunucu: korumalı dizin ---------------------------------------------------


def _check_private(path: Path, *, kind: str) -> None:
    try:
        info = os.lstat(path)
    except OSError as error:
        raise KeychainError(f"Sır {kind} okunamadı: {path} ({error.strerror})") from None
    if stat.S_ISLNK(info.st_mode):
        raise KeychainError(f"Sır {kind} bir bağlantı (symlink); güvenli değil: {path}")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise KeychainError(
            f"Sır {kind} bu uygulamanın kullanıcısına ait değil: {path}. Sahibini "
            "uygulamanın kullanıcısı yapın (kurulum rehberindeki chown adımı)."
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        wanted = "700" if kind == "dizini" else "600"
        raise KeychainError(
            f"Sır {kind} başkalarınca okunabilir ({oct(mode)}): {path}. "
            f"Düzeltin: chmod {wanted} {path}"
        )


def _base(folder: Path, *, create: bool) -> Path:
    if not folder.exists():
        if not create:
            raise KeychainError(f"Sır dizini yok: {folder}")
        try:
            folder.mkdir(mode=0o700)
        except OSError as error:
            raise KeychainError(
                f"Sır dizini oluşturulamadı: {folder} ({error.strerror})"
            ) from None
    _check_private(folder, kind="dizini")
    return folder


def _file_read(folder: Path, service: str, account: str) -> SecretText | None:
    if not folder.exists():
        return None
    base = _base(folder, create=False)
    service_dir = base / service
    if not service_dir.exists():
        return None
    _check_private(service_dir, kind="dizini")
    path = service_dir / account
    if not path.exists():
        return None
    _check_private(path, kind="dosyası")
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        raise KeychainError(f"Sır dosyası okunamadı: {path}") from None
    return SecretText(value) if value else None


def _file_write(folder: Path, service: str, account: str, value: str) -> None:
    base = _base(folder, create=True)
    service_dir = base / service
    if not service_dir.exists():
        try:
            service_dir.mkdir(mode=0o700)
        except OSError as error:
            raise KeychainError(
                f"Sır dizini oluşturulamadı: {service_dir} ({error.strerror})"
            ) from None
    _check_private(service_dir, kind="dizini")
    try:
        # mkstemp dosyayı 600 izniyle açar.
        handle, temporary = tempfile.mkstemp(dir=service_dir, prefix=".yeni-")
        try:
            with os.fdopen(handle, "w", encoding="ascii") as out:
                out.write(value)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, service_dir / account)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
    except OSError as error:
        raise KeychainError(
            f"Sır dizinine yazılamadı: {service_dir} ({error.strerror})"
        ) from None
    stored = _file_read(folder, service, account)
    if stored is None or stored.reveal() != value:
        raise KeychainError("Sır dizinine yazıldı sanıldı ama geri okunamadı.")


def _file_delete(folder: Path, service: str, account: str) -> bool:
    path = folder / service / account
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


# --- ortak -------------------------------------------------------------------


def write(service: str, account: str, secret: SecretText) -> None:
    """Kaydı ekler ya da günceller (Anahtar Zinciri'nde ``-U``)."""
    _check_names(service, account)
    value = secret.reveal()
    if not _SAFE.match(value):
        raise KeychainError(
            f"Değer beklenmeyen karakterler içeriyor; {where('e')} yazılmadı."
        )
    folder = secret_directory()
    if folder is not None:
        _file_write(folder, service, account, value)
        return
    if not available():
        raise KeychainError("Anahtar Zinciri yalnızca macOS'ta kullanılabilir.")
    command = f'add-generic-password -U -s "{service}" -a "{account}" -w "{value}"\n'
    try:
        result = subprocess.run(
            ["security", "-i"], input=command, capture_output=True, text=True, timeout=30
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise KeychainError(f"Anahtar Zinciri'ne yazılamadı: {type(error).__name__}") from None
    # Çıktıda sır olabilir (etkileşimli kip komutu yansıtabilir); gösterilmez.
    if result.returncode != 0 or "error" in (result.stderr or "").lower():
        raise KeychainError("Anahtar Zinciri'ne yazılamadı (security komutu hata verdi).")
    if read(service, account) is None:
        raise KeychainError("Anahtar Zinciri'ne yazıldı sanıldı ama geri okunamadı.")


def read(service: str, account: str) -> SecretText | None:
    """Kaydı okur; yoksa ``None``. Sunucuda izinler bozuksa ``KeychainError``."""
    _check_names(service, account)
    folder = secret_directory()
    if folder is not None:
        return _file_read(folder, service, account)
    if not available():
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return SecretText(value) if value else None


def delete(service: str, account: str) -> bool:
    _check_names(service, account)
    folder = secret_directory()
    if folder is not None:
        return _file_delete(folder, service, account)
    if not available():
        return False
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


__all__ = [
    "DIRECTORY_ENV",
    "KeychainError",
    "available",
    "delete",
    "read",
    "secret_directory",
    "store_name",
    "unavailable_reason",
    "where",
    "write",
]
