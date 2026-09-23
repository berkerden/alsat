"""macOS Anahtar Zinciri'ne (Keychain) sır yazma, okuma, silme.

Sır **komut satırı argümanı olarak geçmez**. ``security add-generic-password
-w <sır>`` sırrı süreç listesinde (``ps``) görünür kılar; bunun yerine
``security -i`` (etkileşimli kip) çalıştırılır ve komut **standart girdiden**
verilir. Okurken sır yalnızca komutun çıktısıdır.

Sırrın içinde yalnızca güvenli karakterler olabilir (harf, rakam ve
``: _ - + / =``); başka karakter içeren bir değer yazılmaz. Böylece komut
satırının tırnaklanmasıyla oynanamaz.
"""

from __future__ import annotations

import re
import subprocess
import sys

from albsat.exchange.keys import SecretText

_SAFE = re.compile(r"^[A-Za-z0-9:_\-+/=]{8,4096}$")
_NAME = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")


class KeychainError(RuntimeError):
    """Anahtar Zinciri işlemi başarısız; mesaj kullanıcıya gösterilir."""


def available() -> bool:
    return sys.platform == "darwin"


def _check_names(service: str, account: str) -> None:
    if not _NAME.match(service) or not _NAME.match(account):
        raise KeychainError("Anahtar Zinciri kaydının adı geçersiz.")


def write(service: str, account: str, secret: SecretText) -> None:
    """Kaydı ekler ya da günceller (``-U``)."""
    _check_names(service, account)
    value = secret.reveal()
    if not _SAFE.match(value):
        raise KeychainError(
            "Değer beklenmeyen karakterler içeriyor; Anahtar Zinciri'ne yazılmadı."
        )
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
    """Kaydı okur; yoksa ``None``."""
    _check_names(service, account)
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


__all__ = ["KeychainError", "available", "delete", "read", "write"]
