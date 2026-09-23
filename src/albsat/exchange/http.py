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
from collections.abc import Sequence
from typing import Any

from albsat.data.backfill import MAX_LIMIT
from albsat.exchange.ratelimit import BudgetExceeded, RequestBudget, parse_retry_after

USER_AGENT = "albsat/0.1 (+https://github.com/)"


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str) -> None:
        # 404'te sunucunun XML gövdesini basmak gereksiz gürültü; dosyanın
        # bulunamadığını bilmek yeterli.
        detail = "" if status == 404 else f"\n{body[:400]}"
        super().__init__(f"HTTP {status} — {url}{detail}")
        self.status = status
        self.url = url
        self.body = body


def _request(
    url: str,
    *,
    timeout: float,
    retries: int,
    backoff: float,
    budget: RequestBudget | None = None,
    weight: int = 1,
) -> bytes:
    """Üstel geri çekilmeli GET.

    429 (rate limit) ve 418 (IP banı) ayrı ele alınır: 418 geldiğinde
    yeniden denemek durumu kötüleştirir, hemen yükseltilir.

    ``budget`` verilirse her denemeden **önce** bütçeden ağırlık ayrılır;
    bütçe izin vermezse istek hiç gönderilmez (``BudgetExceeded``). 429'da
    bekleme bütçeye bırakılır ve burada yeniden denenmez: canlı döngü,
    beklemeyi uyuyarak değil sonraki turda bütçeye sorarak yapar.
    """
    last: Exception | None = None
    path = urllib.parse.urlsplit(url).path
    for attempt in range(retries + 1):
        if budget is not None:
            budget.reserve(weight, path)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if budget is not None:
                    budget.observe(dict(response.headers.items()))
                data: bytes = response.read()
                return data
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            headers = dict(error.headers.items()) if error.headers else {}
            if error.code == 418:
                if budget is not None:
                    budget.banned(parse_retry_after(headers))
                raise HttpError(
                    418, url,
                    "Binance bu IP'yi geçici olarak engelledi (418). Tüm "
                    "isteklerin durdurulması gerekir.\n" + body,
                ) from error
            if error.code == 429 and budget is not None:
                budget.rate_limited(parse_retry_after(headers))
                raise HttpError(429, url, body) from error
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
        budget: RequestBudget | None = None,
    ) -> None:
        self.rest_base = rest_base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        #: Verilirse bütün REST istekleri bu bütçeden geçer (canlı döngü).
        self.budget = budget

    # albsat.data.vision.Downloader arayüzü
    def get(self, url: str) -> bytes:
        return _request(url, timeout=self.timeout, retries=self.retries,
                        backoff=self.backoff)

    def _get_json(
        self, path: str, params: dict[str, Any] | None = None, *, weight: int = 1
    ) -> Any:
        query = urllib.parse.urlencode(
            {k: v for k, v in (params or {}).items() if v is not None}
        )
        url = f"{self.rest_base}{path}" + (f"?{query}" if query else "")
        body = _request(
            url,
            timeout=self.timeout,
            retries=self.retries,
            backoff=self.backoff,
            budget=self.budget,
            weight=weight,
        )
        return json.loads(body)

    def server_time(self) -> int:
        """``GET /api/v3/time`` — sunucu saati (ms), ağırlık 1."""
        return int(self._get_json("/v3/time", weight=1)["serverTime"])

    def exchange_info(self, symbols: Sequence[str] | None = None) -> dict[str, Any]:
        """``GET /api/v3/exchangeInfo`` — sembol filtreleri ve izinleri, ağırlık 20."""
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = json.dumps(list(symbols), separators=(",", ":"))
        payload: dict[str, Any] = self._get_json("/v3/exchangeInfo", params, weight=20)
        return payload

    def book_tickers(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """``GET /api/v3/ticker/bookTicker`` — en iyi alış/satış, birden çok sembolde
        ağırlık 4."""
        params = {"symbols": json.dumps(list(symbols), separators=(",", ":"))}
        return list(self._get_json("/v3/ticker/bookTicker", params, weight=4))

    def tickers_24h(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """``GET /api/v3/ticker/24hr`` (``type=MINI``) — 24 saatlik hacim ve son
        fiyat. 1-20 sembolde ağırlık 2."""
        if len(symbols) > 20:
            raise ValueError("Bu çağrı en fazla 20 sembol içindir (ağırlık 2).")
        params = {
            "symbols": json.dumps(list(symbols), separators=(",", ":")),
            "type": "MINI",
        }
        return list(self._get_json("/v3/ticker/24hr", params, weight=2))

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
        rows: list[list[Any]] = self._get_json(
            "/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
                "limit": min(limit, MAX_LIMIT),
            },
            weight=2,
        )
        return rows


__all__ = ["BudgetExceeded", "HttpError", "PublicHttp", "RequestBudget"]
