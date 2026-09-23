"""API anahtarının güvenli yüklenmesi ve izin doğrulaması.

**Anahtar hiçbir zaman koda, depoya, loga, sohbete veya arayüze yazılmaz**
(SPEC.md §5 ve §11). Bu modül anahtarı yalnızca çalışma anında, iki kaynaktan
birinden okur:

* **macOS:** Keychain (``security find-generic-password``)
* **VPS:** izinleri ``600`` olan şifreli/korumalı bir dosya

Gizli anahtar hiçbir zaman bir ortam değişkeninin *içinde* taşınmaz; ortam
değişkeni yalnızca anahtarın **nerede durduğunu** söyler. Bir sır, süreç
listesinde ve çökme raporlarında görünebildiği için ortam değişkenine
yazılmaz.

``Ed25519PrivateKey`` nesnesinin ``repr``'ı maskelenmiştir: yanlışlıkla
``print`` veya ``logging`` çağrısına düşse bile anahtar sızmaz.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: Anahtarın Keychain'deki servis adını taşıyan ortam değişkeni.
ENV_KEYCHAIN_SERVICE = "ALBSAT_KEYCHAIN_SERVICE"
#: Özel anahtar dosyasının yolunu taşıyan ortam değişkeni (VPS).
ENV_PRIVATE_KEY_PATH = "ALBSAT_PRIVATE_KEY_PATH"
#: Genel API anahtarı kimliği (gizli değildir, imza anahtarı değildir).
ENV_API_KEY = "ALBSAT_API_KEY"


class KeyError_(RuntimeError):
    """Anahtar yüklenemedi veya güvenli değil."""


class WithdrawalPermissionError(RuntimeError):
    """Anahtarda para çekme izni açık — uygulama çalışmayı reddeder."""


@dataclass(frozen=True)
class SecretText:
    """Yanlışlıkla yazdırılmaya karşı maskelenmiş metin."""

    _value: str

    def reveal(self) -> str:
        """Gerçek değeri döndürür. Yalnızca imzalama anında çağrılır."""
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - davranış testte doğrulanıyor
        return "<SecretText gizli>"

    __str__ = __repr__


@dataclass(frozen=True)
class ApiCredentials:
    api_key: str
    private_key: SecretText

    def __repr__(self) -> str:
        masked = (
            f"{self.api_key[:4]}…{self.api_key[-4:]}"
            if len(self.api_key) > 8
            else "…"
        )
        return f"ApiCredentials(api_key={masked}, private_key=<gizli>)"

    __str__ = __repr__


def _read_keychain(service: str, account: str | None = None) -> str:
    """macOS Keychain'den okur.

    Anahtar komut satırı argümanı olarak **geçmez**, yalnızca çıktı olarak
    döner; süreç listesinde görünmez.
    """
    command = ["security", "find-generic-password", "-s", service, "-w"]
    if account:
        command[2:2] = ["-a", account]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError:  # macOS dışı
        raise KeyError_(
            "Keychain yalnızca macOS'ta kullanılabilir. VPS'te "
            f"{ENV_PRIVATE_KEY_PATH} ile dosya yolu verin."
        ) from None
    except subprocess.CalledProcessError as error:
        raise KeyError_(
            f"Keychain'de '{service}' adlı kayıt bulunamadı. Anahtarı şu komutla "
            f"ekleyin:\n  security add-generic-password -s {service} "
            "-a <hesap> -w"
        ) from error
    return result.stdout.strip()


def _read_key_file(path: Path) -> str:
    if not path.exists():
        raise KeyError_(f"Özel anahtar dosyası bulunamadı: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise KeyError_(
            f"{path} dosyasının izinleri çok geniş ({oct(mode)}). "
            f"Düzeltin:  chmod 600 {path}"
        )
    return path.read_text().strip()


def load_credentials(env: Mapping[str, str] | None = None) -> ApiCredentials:
    """Anahtarı Keychain'den veya korumalı dosyadan yükler.

    Ortam değişkenleri yalnızca **konum** bilgisi taşır; sırrın kendisi
    ortamda tutulmaz.
    """
    env = os.environ if env is None else env

    api_key = env.get(ENV_API_KEY, "").strip()
    if not api_key:
        raise KeyError_(
            f"{ENV_API_KEY} tanımlı değil. Bu, gizli olmayan API anahtarı "
            "kimliğidir; imza anahtarı ayrıca Keychain'de veya korumalı bir "
            "dosyada durur."
        )

    service = env.get(ENV_KEYCHAIN_SERVICE, "").strip()
    key_path = env.get(ENV_PRIVATE_KEY_PATH, "").strip()

    if service:
        private = _read_keychain(service)
    elif key_path:
        private = _read_key_file(Path(key_path))
    else:
        raise KeyError_(
            f"Özel anahtarın konumu belirtilmemiş. macOS'ta "
            f"{ENV_KEYCHAIN_SERVICE}, VPS'te {ENV_PRIVATE_KEY_PATH} tanımlayın."
        )

    if not private:
        raise KeyError_("Özel anahtar boş okundu.")

    return ApiCredentials(api_key=api_key, private_key=SecretText(private))


def looks_like_ed25519(private_key_pem: str) -> bool:
    """Anahtarın Ed25519 PEM formatında olup olmadığını kabaca kontrol eder.

    Binance'in HMAC anahtarları düz bir karakter dizisidir ve
    ``session.logon`` ile ``userDataStream.subscribe`` için **kullanılamaz**;
    bu iki özellik yalnızca Ed25519 destekler.
    """
    return "BEGIN" in private_key_pem and "PRIVATE KEY" in private_key_pem


def assert_no_withdrawal_permission(account_payload: Mapping[str, object]) -> None:
    """``GET /api/v3/account`` yanıtını denetler; çekim izni açıksa durdurur.

    DİKKAT (23 Eylül 2026): ``canWithdraw`` hesabın bayrağıdır, API
    anahtarının izni değil; Binance Demo hesabı bile ``true`` döndürüyor.
    Anahtarın çekim izni ``/sapi/v1/account/apiRestrictions``'taki
    ``enableWithdrawals`` ile denetlenir (``signed.restriction_problems``).
    Bu fonksiyon hiçbir yerden çağrılmıyor; Faz 6'da onun yerine o kullanılmalı.

    SPEC.md §5: "Uygulama açılışta anahtarın izinlerini kontrol etsin; çekim
    izni açıksa çalışmayı reddetsin."
    """
    if account_payload.get("canWithdraw"):
        raise WithdrawalPermissionError(
            "Bu API anahtarında para çekme izni AÇIK. Uygulama bu anahtarla "
            "çalışmayı reddediyor.\n"
            "Binance → API Yönetimi → anahtarı düzenle → 'Para Çekme' iznini "
            "kapatın, ya da izinleri doğru olan yeni bir anahtar oluşturun."
        )
    if not account_payload.get("canTrade"):
        raise KeyError_(
            "Bu API anahtarında Spot işlem izni kapalı; emir gönderilemez."
        )
