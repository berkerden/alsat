"""Emir kimlikleri (Demo ve canlı).

Borsa kuralı: kimlik ``^[a-zA-Z0-9-_]{1,36}$``. Biçim::

    <önek><jeton>-<rol>

* ``önek`` hesabı söyler: ``albsat-demo-`` Binance Demo Mode (sahte para),
  ``albsat-canli-`` canlı hesap (gerçek para). İki hesabın kimlikleri hiç
  karışmaz; her yürütücü yalnızca kendi önekini taşıyan emre dokunur.
* ``jeton`` pozisyonun kimliğidir (zaman + rastgele, 12 karakter). Aynı
  pozisyonun bütün emirleri aynı jetonu taşır; borsadaki bir emirden
  pozisyon bulunur.
* ``rol`` emrin görevini ve deneme numarasını söyler: ``L1`` giriş listesi,
  ``G1`` giriş, ``H1`` hedef, ``S1`` stop (OTOCO); ``K2L``/``K2H``/``K2S``
  yeniden kurulan koruma (OCO); ``C3`` korumalı çıkış (IOC).

Kimlik emir gönderilmeden **önce** diske yazılır. Gönderim sonucu
bilinmezse aynı kimlikle sorgulanır; emir yeni bir kimlikle yeniden
gönderilmez. Böylece aynı pozisyon için borsada iki giriş emri oluşamaz.

Modül düzeyindeki işlevler (``client_id``, ``otoco_ids`` ...) Faz 5'ten
kalan Demo kısayollarıdır; canlı kod ``LIVE`` şemasını açıkça kullanır.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass

from albsat.exchange.trading import DEMO_PREFIX, LIVE_PREFIX

_VALID = re.compile(r"^[a-zA-Z0-9\-_]{1,36}$")
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

ROLE_LIST = "L"
ROLE_ENTRY = "G"
ROLE_TARGET = "H"
ROLE_STOP = "S"
ROLE_REARM = "K"
ROLE_EXIT = "C"


def _base36(value: int) -> str:
    digits = []
    while value:
        value, rest = divmod(value, 36)
        digits.append(_ALPHABET[rest])
    return "".join(reversed(digits)) or "0"


def new_token() -> str:
    """Pozisyon jetonu: 8 karakter zaman (ms, 36 tabanı) + 4 rastgele.

    En uzun kimlik ``albsat-canli-`` + 12 + ``-K99L`` = 31 karakter (sınır 36)."""
    stamp = _base36(int(time.time() * 1000))[-8:]
    return stamp + "".join(_ALPHABET[secrets.randbelow(36)] for _ in range(4))


@dataclass(frozen=True)
class IdScheme:
    """Bir hesabın (Demo ya da canlı) kimlik öneki ve kimlik üretimi."""

    prefix: str

    def client_id(self, token: str, role: str) -> str:
        value = f"{self.prefix}{token}-{role}"
        if not _VALID.match(value):
            raise ValueError(f"Geçersiz emir kimliği: {value}")
        return value

    def otoco_ids(self, token: str, attempt: int) -> dict[str, str]:
        return {
            "listClientOrderId": self.client_id(token, f"{ROLE_LIST}{attempt}"),
            "workingClientOrderId": self.client_id(token, f"{ROLE_ENTRY}{attempt}"),
            "pendingAboveClientOrderId": self.client_id(token, f"{ROLE_TARGET}{attempt}"),
            "pendingBelowClientOrderId": self.client_id(token, f"{ROLE_STOP}{attempt}"),
        }

    def oco_ids(self, token: str, attempt: int) -> dict[str, str]:
        return {
            "listClientOrderId": self.client_id(token, f"{ROLE_REARM}{attempt}{ROLE_LIST}"),
            "aboveClientOrderId": self.client_id(token, f"{ROLE_REARM}{attempt}{ROLE_TARGET}"),
            "belowClientOrderId": self.client_id(token, f"{ROLE_REARM}{attempt}{ROLE_STOP}"),
        }

    def exit_id(self, token: str, attempt: int) -> str:
        return self.client_id(token, f"{ROLE_EXIT}{attempt}")

    def token_of(self, value: str) -> str | None:
        """Kimlikten pozisyon jetonu; bu hesabın kimliği değilse ``None``."""
        if not value.startswith(self.prefix):
            return None
        rest = value[len(self.prefix):]
        token, _, _ = rest.partition("-")
        return token or None

    def is_ours(self, value: str | None) -> bool:
        return bool(value) and str(value).startswith(self.prefix)


DEMO = IdScheme(DEMO_PREFIX)
LIVE = IdScheme(LIVE_PREFIX)

# --- Faz 5 kısayolları (Demo) ------------------------------------------------------

client_id = DEMO.client_id
otoco_ids = DEMO.otoco_ids
oco_ids = DEMO.oco_ids
exit_id = DEMO.exit_id
token_of = DEMO.token_of
is_ours = DEMO.is_ours


__all__ = [
    "DEMO",
    "LIVE",
    "ROLE_ENTRY",
    "ROLE_EXIT",
    "ROLE_LIST",
    "ROLE_REARM",
    "ROLE_STOP",
    "ROLE_TARGET",
    "IdScheme",
    "client_id",
    "exit_id",
    "is_ours",
    "new_token",
    "oco_ids",
    "otoco_ids",
    "token_of",
]
