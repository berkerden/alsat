"""Bildirim arayüzü.

Kâğıt işlem motoru ve canlı döngü bildirimi bu arayüz üzerinden gönderir;
Telegram'ın kurulu olup olmadığını bilmez. Telegram kurulu değilse
``MemoryNotifier`` kullanılır: bildirimler yine üretilir ve arayüzün "son
bildirimler" listesinde görünür, yalnızca telefona gitmez. Böylece
"bildirim gelmedi" ile "bildirim hiç üretilmedi" ayrılabilir.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from albsat.core.clock import iso, utc_now

KIND_SIGNAL = "sinyal"
KIND_ORDER = "emir"
KIND_FILL = "dolum"
KIND_TARGET = "hedef"
KIND_STOP = "stop"
KIND_EXIT = "cikis"
KIND_CANCEL = "iptal"
KIND_LIMIT = "limit"
KIND_KILL = "acil_durdur"
KIND_CONNECTION = "baglanti"
KIND_MODE = "mod"
KIND_SYSTEM = "sistem"


@dataclass(frozen=True)
class Notice:
    zaman_utc: str
    tur: str
    metin: str
    #: Telegram'a gönderildi mi? Telegram kurulu değilse ``False``.
    gonderildi: bool = False


class Notifier(Protocol):
    def send(self, text: str, *, kind: str = KIND_SYSTEM) -> None: ...

    def recent(self, limit: int = 50) -> tuple[Notice, ...]: ...


class MemoryNotifier:
    """Bildirimleri yalnızca bellekte tutar (Telegram kurulu değilken ve testlerde)."""

    def __init__(self, capacity: int = 200) -> None:
        self._items: deque[Notice] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def send(self, text: str, *, kind: str = KIND_SYSTEM) -> None:
        self._record(Notice(iso(utc_now()), kind, text, gonderildi=False))

    def _record(self, notice: Notice) -> None:
        with self._lock:
            self._items.append(notice)

    def recent(self, limit: int = 50) -> tuple[Notice, ...]:
        with self._lock:
            items = list(self._items)[-limit:]
        return tuple(reversed(items))

    @property
    def texts(self) -> list[str]:
        with self._lock:
            return [item.metin for item in self._items]


__all__ = [
    "KIND_CANCEL",
    "KIND_CONNECTION",
    "KIND_EXIT",
    "KIND_FILL",
    "KIND_KILL",
    "KIND_LIMIT",
    "KIND_MODE",
    "KIND_ORDER",
    "KIND_SIGNAL",
    "KIND_STOP",
    "KIND_SYSTEM",
    "KIND_TARGET",
    "MemoryNotifier",
    "Notice",
    "Notifier",
]
