"""Binance'e giden istek hacminin sınırı.

Binance IP başına dakikada 6000 ağırlık tanır; aşan istemci önce 429 alır,
429'a rağmen istek göndermeye devam ederse IP'si 418 ile 2 dakikadan 3 güne
kadar engellenir (``rest-api.md``, "IP Limits"). Bu uygulamanın normal
çalışması dakikada birkaç on ağırlık harcar. Bu yüzden buradaki sınır
borsanınkinin çok altındadır: bir hata, döngü ya da beklenmedik büyüklükte
bir iş (ör. aylarca geriye veri çekmek) istek **gönderilmeden** durdurulur.

Üç kural:

1. **Yerel tavan.** Son 60 saniyede harcanan ağırlık + yeni isteğin ağırlığı
   tavanı aşacaksa istek gönderilmez, ``BudgetExceeded`` yükseltilir.
2. **429 → bekle.** Borsa ``Retry-After`` başlığıyla ne kadar bekleneceğini
   söyler; o süre dolana kadar hiçbir istek gönderilmez.
3. **418 → dur.** Engel süresince hiçbir istek gönderilmez, durum arayüzde
   ve bildirimde görünür. Engel sırasında istek göndermek süreyi uzatır.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

#: Borsanın dakikalık ağırlık sınırı (exchangeInfo ``REQUEST_WEIGHT``).
EXCHANGE_LIMIT_PER_MINUTE = 6000

#: Bu uygulamanın kendine koyduğu tavan: borsanın onda biri.
DEFAULT_LOCAL_CAP = 600

WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"


class BudgetExceeded(RuntimeError):
    """İstek gönderilmedi: yerel tavan, 429 beklemesi ya da 418 engeli."""

    def __init__(self, message: str, *, retry_in: float) -> None:
        super().__init__(message)
        self.retry_in = retry_in


@dataclass(frozen=True)
class BudgetStatus:
    yerel_1dk: int
    yerel_tavan: int
    borsa_1dk: int | None
    toplam_istek: int
    reddedilen: int
    bekleme_saniye: float
    engelli: bool
    son_hata: str | None


class RequestBudget:
    def __init__(
        self,
        *,
        local_cap: int = DEFAULT_LOCAL_CAP,
        clock: Callable[[], float] = time.monotonic,
        on_ban: Callable[[str], None] | None = None,
    ) -> None:
        self.local_cap = local_cap
        self.clock = clock
        self.on_ban = on_ban
        self._lock = threading.Lock()
        self._spent: deque[tuple[float, int]] = deque()
        self._blocked_until = 0.0
        self._banned = False
        self._server_used: int | None = None
        self._total = 0
        self._refused = 0
        self._last_error: str | None = None

    def _used(self, now: float) -> int:
        while self._spent and now - self._spent[0][0] >= 60.0:
            self._spent.popleft()
        return sum(weight for _, weight in self._spent)

    def reserve(self, weight: int, what: str) -> None:
        """İstekten önce çağrılır; gönderilemezse ``BudgetExceeded`` yükseltir."""
        with self._lock:
            now = self.clock()
            if now < self._blocked_until:
                self._refused += 1
                wait = self._blocked_until - now
                reason = "IP engeli (418)" if self._banned else "hız sınırı beklemesi (429)"
                raise BudgetExceeded(
                    f"{what} gönderilmedi: {reason}, {wait:.0f} sn kaldı.", retry_in=wait
                )
            self._banned = False
            used = self._used(now)
            if used + weight > self.local_cap:
                self._refused += 1
                wait = 60.0 - (now - self._spent[0][0]) if self._spent else 60.0
                self._last_error = (
                    f"Yerel istek tavanı: son 1 dakikada {used} ağırlık harcandı, "
                    f"tavan {self.local_cap}. {what} gönderilmedi."
                )
                raise BudgetExceeded(self._last_error, retry_in=max(wait, 1.0))
            self._spent.append((now, weight))
            self._total += 1

    def observe(self, headers: Mapping[str, str] | None) -> None:
        """Borsanın bildirdiği kullanılan ağırlığı kaydeder."""
        if not headers:
            return
        for key, value in headers.items():
            if key.lower() == WEIGHT_HEADER.lower():
                with contextlib.suppress(ValueError):
                    self._server_used = int(value)

    def rate_limited(self, retry_after: float | None) -> None:
        """429: borsanın söylediği süre kadar (yoksa 60 sn) istek yok."""
        with self._lock:
            wait = retry_after if retry_after and retry_after > 0 else 60.0
            self._blocked_until = max(self._blocked_until, self.clock() + wait)
            self._last_error = f"Binance hız sınırı (429); {wait:.0f} sn bekleniyor."

    def banned(self, retry_after: float | None) -> None:
        """418: engel süresince hiçbir istek yok."""
        with self._lock:
            wait = retry_after if retry_after and retry_after > 0 else 300.0
            self._blocked_until = max(self._blocked_until, self.clock() + wait)
            self._banned = True
            self._last_error = (
                f"Binance bu IP'yi geçici olarak engelledi (418); {wait:.0f} sn boyunca "
                "hiç istek gönderilmeyecek."
            )
            message = self._last_error
        if self.on_ban is not None:
            self.on_ban(message)

    def status(self) -> BudgetStatus:
        with self._lock:
            now = self.clock()
            return BudgetStatus(
                yerel_1dk=self._used(now),
                yerel_tavan=self.local_cap,
                borsa_1dk=self._server_used,
                toplam_istek=self._total,
                reddedilen=self._refused,
                bekleme_saniye=max(0.0, self._blocked_until - now),
                engelli=self._banned and now < self._blocked_until,
                son_hata=self._last_error,
            )


def parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                return None
    return None


__all__ = [
    "DEFAULT_LOCAL_CAP",
    "EXCHANGE_LIMIT_PER_MINUTE",
    "BudgetExceeded",
    "BudgetStatus",
    "RequestBudget",
    "parse_retry_after",
]
