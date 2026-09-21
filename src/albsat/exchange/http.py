"""Kimlik doğrulaması gerektirmeyen HTTP erişimi (yalnızca standart kütüphane).

Bu modül **imzalı** hiçbir istek yapmaz; sadece herkese açık uç noktaları
(``exchangeInfo``, ``klines``, ``time``) ve ``data.binance.vision`` arşivlerini
okur. İmzalı istekler ve emir gönderimi Faz 5'te resmi
``binance-sdk-spot`` üzerinden eklenecek; bu modülün API anahtarına erişimi
yoktur ve olmamalıdır.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Sequence

from albsat.data.backfill import MAX_LIMIT

USER_AGENT = "albsat/0.1 (+https://github.com/)"


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str) -> None:
        super().__init__(f"HTTP {status} — {url}\n{body[:400]}")
        self.status = status
        self.url = url
        self.body = body


def _request(url: str, *, timeout: float, retries: int, backoff: float) -> bytes:
    """Üstel geri çekilmeli GET.

    429 (rate limit) ve 418 (IP banı) ayrı ele alınır: 418 geldiğinde
    yeniden denemek durumu kötüleştirir, hemen yükseltilir.
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            if error.code == 418:
                raise HttpError(
                    418, url,
                    "Binance bu IP'yi geçici olarak engelledi (418). Tüm "
                    "isteklerin durdurulması gerekir.\n" + body,
                ) from error
            if error.code in (429, 500, 502, 503, 504) and attempt < retries:
                last = error
                time.sleep(backoff * (2**attempt))
                continue
            raise HttpError(error.code, url, body) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt < retries:
                last = error
                time.sleep(backoff * (2**attempt))
                continue
            raise
    raise RuntimeError(f"İstek başarısız: {url}") from last


class PublicHttp:
    """``data.binance.vision`` ve herkese açık REST uç noktaları için istemci."""

    def __init__(
        self,
        *,
        rest_base: str = "https://api.binance.com/api",
        timeout: float = 30.0,
        retries: int = 4,
        backoff: float = 2.0,
    ) -> None:
        self.rest_base = rest_base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    # albsat.data.vision.Downloader arayüzü
    def get(self, url: str) -> bytes:
        return _request(url, timeout=self.timeout, retries=self.retries,
                        backoff=self.backoff)

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(
            {k: v for k, v in (params or {}).items() if v is not None}
        )
        url = f"{self.rest_base}{path}" + (f"?{query}" if query else "")
        return json.loads(self.get(url))

    def server_time(self) -> int:
        """``GET /api/v3/time`` — sunucu saati (ms)."""
        return int(self._get_json("/v3/time")["serverTime"])

    def exchange_info(self, symbols: Sequence[str] | None = None) -> dict[str, Any]:
        """``GET /api/v3/exchangeInfo`` — sembol filtreleri ve izinleri."""
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = json.dumps(list(symbols), separators=(",", ":"))
        return self._get_json("/v3/exchangeInfo", params)

    # albsat.data.backfill.KlineSource arayüzü
    def klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = MAX_LIMIT,
    ) -> list[list[Any]]:
        """``GET /api/v3/klines`` — ağırlık 2, azami limit 1000."""
        return self._get_json(
            "/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
                "limit": min(limit, MAX_LIMIT),
            },
        )
