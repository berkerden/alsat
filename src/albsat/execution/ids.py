"""Demo emir kimlikleri.

Borsa kuralı: kimlik ``^[a-zA-Z0-9-_]{1,36}$``. Biçim::

    albsat-demo-<jeton>-<rol>

* ``jeton`` pozisyonun kimliğidir (zaman + rastgele, 12 karakter). Aynı
  pozisyonun bütün emirleri aynı jetonu taşır; borsadaki bir emirden
  pozisyon bulunur.
* ``rol`` emrin görevini ve deneme numarasını söyler: ``L1`` giriş listesi,
  ``G1`` giriş, ``H1`` hedef, ``S1`` stop (OTOCO); ``K2L``/``K2H``/``K2S``
  yeniden kurulan koruma (OCO); ``C3`` korumalı çıkış (IOC).

Kimlik emir gönderilmeden **önce** diske yazılır. Gönderim sonucu
bilinmezse aynı kimlikle sorgulanır; emir yeni bir kimlikle yeniden
gönderilmez. Böylece aynı pozisyon için borsada iki giriş emri oluşamaz.
"""

from __future__ import annotations

import re
import secrets
import time

from albsat.exchange.trading import CLIENT_PREFIX

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

    En uzun kimlik ``albsat-demo-`` + 12 + ``-K99L`` = 30 karakter (sınır 36)."""
    stamp = _base36(int(time.time() * 1000))[-8:]
    return stamp + "".join(_ALPHABET[secrets.randbelow(36)] for _ in range(4))


def client_id(token: str, role: str) -> str:
    value = f"{CLIENT_PREFIX}{token}-{role}"
    if not _VALID.match(value):
        raise ValueError(f"Geçersiz emir kimliği: {value}")
    return value


def otoco_ids(token: str, attempt: int) -> dict[str, str]:
    return {
        "listClientOrderId": client_id(token, f"{ROLE_LIST}{attempt}"),
        "workingClientOrderId": client_id(token, f"{ROLE_ENTRY}{attempt}"),
        "pendingAboveClientOrderId": client_id(token, f"{ROLE_TARGET}{attempt}"),
        "pendingBelowClientOrderId": client_id(token, f"{ROLE_STOP}{attempt}"),
    }


def oco_ids(token: str, attempt: int) -> dict[str, str]:
    return {
        "listClientOrderId": client_id(token, f"{ROLE_REARM}{attempt}{ROLE_LIST}"),
        "aboveClientOrderId": client_id(token, f"{ROLE_REARM}{attempt}{ROLE_TARGET}"),
        "belowClientOrderId": client_id(token, f"{ROLE_REARM}{attempt}{ROLE_STOP}"),
    }


def exit_id(token: str, attempt: int) -> str:
    return client_id(token, f"{ROLE_EXIT}{attempt}")


def token_of(value: str) -> str | None:
    """Kimlikten pozisyon jetonu; bu uygulamanın kimliği değilse ``None``."""
    if not value.startswith(CLIENT_PREFIX):
        return None
    rest = value[len(CLIENT_PREFIX):]
    token, _, _ = rest.partition("-")
    return token or None


def is_ours(value: str | None) -> bool:
    return bool(value) and str(value).startswith(CLIENT_PREFIX)


__all__ = [
    "ROLE_ENTRY",
    "ROLE_EXIT",
    "ROLE_LIST",
    "ROLE_REARM",
    "ROLE_STOP",
    "ROLE_TARGET",
    "client_id",
    "exit_id",
    "is_ours",
    "new_token",
    "oco_ids",
    "otoco_ids",
    "token_of",
]
