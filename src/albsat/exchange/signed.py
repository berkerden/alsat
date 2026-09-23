"""İmzalı, **yalnızca okuyan** Binance istekleri (Ed25519).

Faz 4'te iki şey okunur, başka hiçbir şey:

* ``GET /sapi/v1/account/apiRestrictions`` (ağırlık 1): anahtarın izinleri.
  Para çekme izni açıksa uygulama anahtarı kullanmayı reddeder (SPEC §5).
* ``GET /api/v3/account/commission`` (ağırlık 20): hesaba özel komisyon.

Bu sınıfta emir gönderen, iptal eden ya da hesabı değiştiren **hiçbir
yöntem yoktur**; izin verilen adresler aşağıdaki listeyle sınırlıdır ve
listede olmayan bir adres istenirse istek gönderilmeden reddedilir.

İmza (``request-security.md``): sorgu metni (``timestamp`` ve
``recvWindow`` dahil) olduğu gibi Ed25519 ile imzalanır, imza base64
yazılır ve ``signature`` parametresi olarak eklenir. Genel anahtar kimliği
``X-MBX-APIKEY`` başlığında gider.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from albsat.core import keychain
from albsat.exchange.http import USER_AGENT, HttpError
from albsat.exchange.keys import SecretText
from albsat.exchange.ratelimit import RequestBudget, parse_retry_after

KEYCHAIN_SERVICE = "albsat-binance"
ACCOUNT_API_KEY = "api-key"
ACCOUNT_PRIVATE = "ed25519-private"

#: İzin verilen imzalı adresler ve ağırlıkları. Hepsi GET.
ALLOWED: Mapping[str, int] = {
    "/sapi/v1/account/apiRestrictions": 1,
    "/api/v3/account/commission": 20,
}

RECV_WINDOW_MS = 5000


class SignedRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredKey:
    api_key: SecretText
    private_der_b64: SecretText

    def __repr__(self) -> str:
        return "StoredKey(<gizli>)"


def load_key(service: str = KEYCHAIN_SERVICE) -> StoredKey | None:
    """Anahtar Zinciri'ndeki anahtar; ``service`` Faz 5'te Demo anahtarı için ayrıdır."""
    api_key = keychain.read(service, ACCOUNT_API_KEY)
    private = keychain.read(service, ACCOUNT_PRIVATE)
    if api_key is None or private is None:
        return None
    return StoredKey(api_key, private)


def generate_keypair() -> tuple[SecretText, str]:
    """Yeni Ed25519 anahtar çifti: (özel anahtar base64 DER, genel anahtar PEM)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    der = private.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    return SecretText(base64.b64encode(der).decode("ascii")), public_pem


def public_pem_of(private_der_b64: SecretText) -> str:
    from cryptography.hazmat.primitives import serialization

    key = _load_private(private_der_b64)
    pem: bytes = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem.decode("ascii")


def _load_private(private_der_b64: SecretText) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_der_private_key(
        base64.b64decode(private_der_b64.reveal()), password=None
    )
    if not isinstance(key, Ed25519PrivateKey):
        raise SignedRequestError("Kayıtlı özel anahtar Ed25519 değil.")
    return key


def sign(query: str, private_der_b64: SecretText) -> str:
    signature = _load_private(private_der_b64).sign(query.encode("ascii"))
    return base64.b64encode(signature).decode("ascii")


class SignedReader:
    def __init__(
        self,
        key: StoredKey,
        *,
        base: str = "https://api.binance.com",
        budget: RequestBudget | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
        time_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        timeout: float = 20.0,
    ) -> None:
        self._key = key
        self.base = base.rstrip("/")
        self.budget = budget
        self.opener = opener
        self.time_ms = time_ms
        self.timeout = timeout
        self.offset_ms = 0

    def __repr__(self) -> str:
        return f"SignedReader({self.base}, <anahtar gizli>)"

    def sync_time(self) -> int:
        """Sunucu saatiyle farkı ölçer (imzalı istekte ``timestamp`` tutsun diye)."""
        url = f"{self.base}/api/v3/time"
        if self.budget is not None:
            self.budget.reserve(1, "/api/v3/time")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        before = self.time_ms()
        with self.opener(request, timeout=self.timeout) as response:
            server = int(json.loads(response.read())["serverTime"])
        after = self.time_ms()
        self.offset_ms = server - (before + after) // 2
        return self.offset_ms

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        if path not in ALLOWED:
            raise SignedRequestError(f"İmzalı istek listesinde olmayan adres: {path}")
        values = {key: value for key, value in (params or {}).items() if value is not None}
        values["recvWindow"] = RECV_WINDOW_MS
        values["timestamp"] = self.time_ms() + self.offset_ms
        query = urllib.parse.urlencode(values)
        signature = urllib.parse.quote(sign(query, self._key.private_der_b64), safe="")
        url = f"{self.base}{path}?{query}&signature={signature}"
        if self.budget is not None:
            self.budget.reserve(ALLOWED[path], path)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "X-MBX-APIKEY": self._key.api_key.reveal()},
            method="GET",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                if self.budget is not None:
                    self.budget.observe(dict(response.headers.items()))
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            headers = dict(error.headers.items()) if error.headers else {}
            if self.budget is not None and error.code == 418:
                self.budget.banned(parse_retry_after(headers))
            elif self.budget is not None and error.code == 429:
                self.budget.rate_limited(parse_retry_after(headers))
            # Adres imza içerir; hata mesajına yalnızca yol yazılır.
            raise HttpError(error.code, path, _explain(body)) from None

    def api_restrictions(self) -> dict[str, Any]:
        result = self.get("/sapi/v1/account/apiRestrictions")
        return result if isinstance(result, dict) else {}

    def commission(self, symbol: str) -> dict[str, Any]:
        result = self.get("/api/v3/account/commission", {"symbol": symbol})
        return result if isinstance(result, dict) else {}


def _explain(body: str) -> str:
    """Binance hata kodlarını Türkçe açıklar (en sık görülenler)."""
    try:
        payload = json.loads(body)
    except ValueError:
        return body[:300]
    code = payload.get("code") if isinstance(payload, dict) else None
    message = payload.get("msg", "") if isinstance(payload, dict) else ""
    hints = {
        -2014: "API anahtarı biçimi hatalı; anahtarı yeniden yapıştırın.",
        -2015: "Anahtar, IP ya da izin geçersiz. Binance'te anahtarın 'Okumayı etkinleştir' "
               "izni açık mı, IP kısıtlaması bu bilgisayarın IP'sini içeriyor mu, bakın.",
        -1021: "Bilgisayarın saati Binance'ten çok farklı. macOS saat ayarlarında 'Saati "
               "otomatik ayarla'yı açın.",
        -1022: "İmza geçersiz. Binance'e yüklenen genel anahtar bu bilgisayardaki özel "
               "anahtarla eşleşmiyor olabilir.",
    }
    hint = hints.get(code) if isinstance(code, int) else None
    return f"{code}: {message}" + (f"\n{hint}" if hint else "")


def restriction_problems(payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(engelleyici sorunlar, uyarılar). Çekim izni açıksa anahtar kullanılmaz."""
    blocking: list[str] = []
    warnings: list[str] = []
    if payload.get("enableWithdrawals"):
        blocking.append("Para çekme izni AÇIK. Uygulama bu anahtarı kullanmayı reddediyor.")
    if not payload.get("enableReading"):
        blocking.append("Okuma izni kapalı; komisyon okunamaz.")
    for key, label in (("enableMargin", "Margin"), ("enableFutures", "Vadeli işlem (futures)")):
        if payload.get(key):
            blocking.append(f"{label} izni açık. Bu uygulama yalnızca Spot kullanır; kapatın.")
    for key, label in (
        ("enableInternalTransfer", "Hesaplar arası transfer"),
        ("permitsUniversalTransfer", "Evrensel transfer"),
        ("enableVanillaOptions", "Opsiyon"),
        ("enablePortfolioMarginTrading", "Portföy margin"),
    ):
        if payload.get(key):
            warnings.append(f"{label} izni açık; bu uygulamanın ihtiyacı yok, kapatmanız önerilir.")
    if payload.get("enableSpotAndMarginTrading"):
        warnings.append(
            "Spot işlem izni açık. Faz 4 yalnızca okur; bu anahtar için işlem iznini "
            "kapatmanız daha güvenli. Emir gönderimi Faz 5'te ayrı bir Demo Mode anahtarıyla "
            "denenecek."
        )
    if not payload.get("ipRestrict"):
        warnings.append(
            "IP kısıtlaması yok. Yalnızca okuma izni olan bir anahtar için kabul edilebilir; "
            "işlem izni verilecek anahtarlarda IP kısıtlaması şart."
        )
    return blocking, warnings


#: Canlı işlem anahtarında açık olmaması gereken izinler (SPEC §5: yalnızca okuma
#: ve Spot işlem; para çekme kesinlikle kapalı, margin/futures kapalı).
LIVE_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("enableWithdrawals", "Para çekme"),
    ("enableMargin", "Margin (borç, geri ödeme, transfer)"),
    ("enableFutures", "Vadeli işlem (futures)"),
    ("enableVanillaOptions", "Opsiyon"),
    ("enablePortfolioMarginTrading", "Portföy margin"),
    ("enableInternalTransfer", "Hesaplar arası transfer"),
    ("permitsUniversalTransfer", "Evrensel transfer"),
)


def live_key_problems(payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Canlı **işlem** anahtarı için (engelleyici sorunlar, uyarılar).

    Faz 4'ün ``restriction_problems``'ı salt okuma anahtarı içindir (işlem iznini
    uyarı sayar). Burada tersi: Spot işlem izni **gerekir**; para çekme, margin,
    vadeli işlem, opsiyon ve transfer izinlerinden biri açıksa anahtar
    kullanılmaz. Karar ``apiRestrictions`` yanıtıyla verilir; hesabın
    ``canWithdraw`` bayrağına bakılmaz (o anahtarın izni değildir).
    """
    blocking: list[str] = []
    warnings: list[str] = []
    if "enableWithdrawals" not in payload:
        blocking.append("Anahtarın izinleri okunamadı; Binance beklenen yanıtı vermedi.")
        return blocking, warnings
    for key, label in LIVE_FORBIDDEN:
        if payload.get(key):
            if key == "enableWithdrawals":
                blocking.append("Para çekme izni AÇIK. Uygulama bu anahtarla emir göndermez; "
                                "Binance'te anahtarın 'Para çekme' iznini kapatın.")
            else:
                blocking.append(f"{label} izni açık. Bu uygulama yalnızca Spot kullanır; "
                                "Binance'te bu izni kapatın.")
    if not payload.get("enableReading"):
        blocking.append("Okuma izni kapalı; bakiye ve emirler okunamaz.")
    if not payload.get("enableSpotAndMarginTrading"):
        blocking.append("Spot işlem izni kapalı; emir gönderilemez. Binance'te anahtarın "
                        "'Spot ve Margin işlemlerini etkinleştir' iznini açın.")
    if not payload.get("ipRestrict"):
        warnings.append(
            "IP kısıtlaması yok: anahtar her IP adresinden kullanılabilir. Özel yarı bu "
            "Mac'in Anahtar Zinciri'nde durduğu için anahtarı kullanmak için bu Mac'e erişmek "
            "gerekir; yine de Binance işlem izinli anahtarlarda güvenilir IP kısıtını "
            "öneriyor. Ev IP'niz değişirse kısıtlı anahtar çalışmayı bırakır (emir "
            "gönderilemez, borsadaki stop ve hedef yerinde kalır)."
        )
    return blocking, warnings


__all__ = [
    "ACCOUNT_API_KEY",
    "ACCOUNT_PRIVATE",
    "ALLOWED",
    "KEYCHAIN_SERVICE",
    "SignedReader",
    "SignedRequestError",
    "StoredKey",
    "LIVE_FORBIDDEN",
    "generate_keypair",
    "live_key_problems",
    "load_key",
    "public_pem_of",
    "restriction_problems",
    "sign",
]
