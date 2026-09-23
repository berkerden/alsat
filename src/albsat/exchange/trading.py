"""İmzalı Binance **Demo Mode** istemcisi: emir gönderir, iptal eder, sorgular.

Faz 5'in borsaya dokunan tek sınıfı ``DemoTrader``'dır. Faz 4'ün salt okuyan
``SignedReader``'ı değiştirilmedi; emir yöntemleri ona eklenmedi. İki sınıfın
izin listeleri ayrıdır ve ikisi de listede olmayan bir adresi istek
göndermeden reddeder.

**Neden resmi SDK değil, kendi küçük istemcimiz?**

1. Berk'in ağında HTTPS trafiği yeniden imzalanıyor. Uygulama macOS güven
   deposunu ``truststore`` ile standart kütüphanenin ``ssl``/``urllib``
   katmanına bağlıyor (``core.tls``) ve bu yol Faz 1'den beri o ağda
   çalışıyor. SDK kendi HTTP katmanını getirir; güven deposunun orada da
   çalıştığını ayrıca kanıtlamak gerekirdi.
2. İzin listesi yapısaldır: bu sınıf yalnızca aşağıdaki ``ALLOWED``
   çiftlerini gönderebilir. SDK'da para çekme, margin, bütün açık emirleri
   silme dahil her uç hazır durur; burada hiçbiri yazılı değildir.
3. Sahte borsayla sınanabilir: ağ katmanı (``opener``) dışarıdan verilir,
   kaos testleri gerçek istek biçimini ve imzayı doğrular.
4. Yeni bağımlılık yok.

**Yalnızca Demo Mode.** Kurucu ``Environment.DEMO`` dışındaki her ortamı
reddeder ve adresi kendisi ``endpoints.py``'den alır; dışarıdan adres
verilemez. Faz 6'da canlı ortam ayrı bir kararla açılacak.

**Yalnızca kendi emirleri.** Gönderilen ve iptal edilen her emrin kimliği
``albsat-demo-`` ile başlamak zorundadır. Kullanıcının elle verdiği emirler
bu önekle başlamadığı için bu sınıf onları iptal edemez. Bütün açık emirleri
tek seferde silen ``DELETE /api/v3/openOrders`` bilerek listede yok.

**Sonucu bilinmeyen istek.** Emir gönderen ya da iptal eden bir istekte ağ
zaman aşımı, HTTP 5xx ya da ``-1006``/``-1007`` gelirse borsa isteği işlemiş
de olabilir, işlememiş de. Binance belgesi bu durumu başarısız saymamayı
söyler. ``OutcomeUnknown`` yükseltilir; çağıran taraf emri kimliğiyle sorgular
ve aynı emri yeni kimlikle yeniden göndermez.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from albsat.exchange.endpoints import Environment, endpoints_for
from albsat.exchange.http import USER_AGENT
from albsat.exchange.ratelimit import RequestBudget, parse_retry_after
from albsat.exchange.signed import RECV_WINDOW_MS, StoredKey, sign

#: Bu uygulamanın Demo Mode emirlerinin kimlik öneki.
CLIENT_PREFIX = "albsat-demo-"

#: Demo anahtarının Anahtar Zinciri kaydı. Faz 4'ün salt okuma anahtarından
#: (``albsat-binance``) ayrıdır; iki anahtar birbirinin yerine kullanılamaz.
KEYCHAIN_SERVICE_DEMO = "albsat-binance-demo"

#: İzin verilen imzalı istekler ve ağırlıkları (``rest-api.md``).
ALLOWED: Mapping[tuple[str, str], int] = {
    ("GET", "/api/v3/account"): 20,
    ("GET", "/api/v3/account/commission"): 20,
    ("GET", "/api/v3/order"): 4,
    ("GET", "/api/v3/orderList"): 4,
    ("GET", "/api/v3/openOrders"): 6,
    ("GET", "/api/v3/myTrades"): 5,
    ("POST", "/api/v3/orderList/otoco"): 1,
    ("POST", "/api/v3/orderList/oco"): 1,
    ("POST", "/api/v3/order"): 1,
    ("DELETE", "/api/v3/order"): 1,
    ("DELETE", "/api/v3/orderList"): 1,
}

#: Sonucu bilinmeyen sayılan hata kodları (``errors.md``).
UNKNOWN_CODES = frozenset({-1006, -1007})

ORDER_COUNT_10S = "x-mbx-order-count-10s"
ORDER_COUNT_1D = "x-mbx-order-count-1d"


class TradingRefused(RuntimeError):
    """İstek bu sınıfın kurallarına uymuyor; hiç gönderilmedi."""


class ExchangeError(RuntimeError):
    """Borsa isteği işledi ve reddetti (ya da istek hiç ulaşmadı: ``status=0``)."""

    def __init__(self, *, status: int, code: int | None, msg: str, method: str,
                 path: str) -> None:
        text = f"{method} {path}: HTTP {status}" + (f", {code}" if code is not None else "")
        super().__init__(f"{text}: {msg}"[:500])
        self.status = status
        self.code = code
        self.msg = msg
        self.method = method
        self.path = path


class OutcomeUnknown(RuntimeError):
    """Emir/iptal isteği borsada işlenmiş olabilir; sonuç sorgulanarak öğrenilir."""

    def __init__(self, *, method: str, path: str, detail: str, sent_ms: int) -> None:
        super().__init__(f"{method} {path}: sonuç bilinmiyor ({detail})"[:500])
        self.method = method
        self.path = path
        self.detail = detail
        #: İstekteki ``timestamp``. Borsa isteği en geç
        #: ``sent_ms + recvWindow``'a kadar kabul eder; sonrası "hiç işlenmedi".
        self.sent_ms = sent_ms


@dataclass(frozen=True)
class OrderCounts:
    """Borsanın son yanıtta bildirdiği emir sayaçları (``X-MBX-ORDER-COUNT-*``)."""

    son_10sn: int | None
    son_1gun: int | None
    zaman: float | None


def _require_prefix(name: str, value: object) -> str:
    text = str(value or "")
    if not text.startswith(CLIENT_PREFIX):
        raise TradingRefused(
            f"{name} '{text}' bu uygulamanın öneki ({CLIENT_PREFIX}) ile başlamıyor; "
            "başka birinin emrine dokunulmaz."
        )
    if len(text) > 36:
        raise TradingRefused(f"{name} 36 karakterden uzun.")
    return text


def _require(params: Mapping[str, Any], name: str, allowed: tuple[str, ...]) -> None:
    value = str(params.get(name, ""))
    if value not in allowed:
        raise TradingRefused(f"{name}={value!r} izinli değil (izinli: {', '.join(allowed)}).")


class DemoTrader:
    """Binance Demo Mode'a imzalı istek gönderen tek sınıf."""

    def __init__(
        self,
        key: StoredKey,
        *,
        environment: Environment = Environment.DEMO,
        budget: RequestBudget | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
        time_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        timeout: float = 15.0,
    ) -> None:
        if environment is not Environment.DEMO:
            raise TradingRefused(
                "Emir gönderimi bu fazda yalnızca Binance Demo Mode'da açık "
                f"(istenen ortam: {environment.value})."
            )
        endpoints = endpoints_for(environment)
        if endpoints.real_money:  # pragma: no cover - endpoints.py değişirse yakalansın
            raise TradingRefused("Demo ortamı gerçek para taşıyor görünüyor; durduruldu.")
        self._key = key
        self.base = endpoints.rest.removesuffix("/api").rstrip("/")
        self.budget = budget or RequestBudget()
        self.opener = opener
        self.time_ms = time_ms
        self.timeout = timeout
        self.offset_ms = 0
        self._lock = threading.Lock()
        self._counts = OrderCounts(None, None, None)

    def __repr__(self) -> str:
        return f"DemoTrader({self.base}, <anahtar gizli>)"

    # --- saat ------------------------------------------------------------

    def now_ms(self) -> int:
        """Sunucu saatine göre şimdiki an (ms)."""
        return self.time_ms() + self.offset_ms

    def sync_time(self) -> int:
        """Sunucu saatiyle farkı ölçer; imzalı isteklerin ``timestamp``'ı buna göre."""
        path = "/api/v3/time"
        self.budget.reserve(1, path)
        request = urllib.request.Request(f"{self.base}{path}", headers={"User-Agent": USER_AGENT})
        before = self.time_ms()
        try:
            with self.opener(request, timeout=self.timeout) as response:
                server = int(json.loads(response.read())["serverTime"])
        except urllib.error.HTTPError as error:
            raise self._http_error(error, "GET", path) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            raise ExchangeError(status=0, code=None, msg=f"{type(error).__name__}: {error}",
                                method="GET", path=path) from None
        after = self.time_ms()
        self.offset_ms = server - (before + after) // 2
        return self.offset_ms

    # --- sayaçlar ---------------------------------------------------------

    def order_counts(self) -> OrderCounts:
        with self._lock:
            return self._counts

    def _observe(self, headers: Mapping[str, str]) -> None:
        self.budget.observe(headers)
        lowered = {key.lower(): value for key, value in headers.items()}
        ten = lowered.get(ORDER_COUNT_10S)
        day = lowered.get(ORDER_COUNT_1D)
        if ten is None and day is None:
            return
        with self._lock:
            self._counts = OrderCounts(
                son_10sn=int(ten) if ten and ten.isdigit() else self._counts.son_10sn,
                son_1gun=int(day) if day and day.isdigit() else self._counts.son_1gun,
                zaman=time.monotonic(),
            )

    # --- çekirdek ----------------------------------------------------------

    def _http_error(self, error: urllib.error.HTTPError, method: str, path: str) -> ExchangeError:
        body = error.read().decode("utf-8", "replace")
        headers = dict(error.headers.items()) if error.headers else {}
        if error.code == 418:
            self.budget.banned(parse_retry_after(headers))
        elif error.code == 429:
            self.budget.rate_limited(parse_retry_after(headers))
        code: int | None = None
        msg = body[:300]
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                raw = payload.get("code")
                code = int(raw) if isinstance(raw, int) else None
                msg = str(payload.get("msg", ""))
        except ValueError:
            pass
        return ExchangeError(status=error.code, code=code, msg=msg, method=method, path=path)

    def request(self, method: str, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """İzin listesindeki bir adrese imzalı istek. ``-1021``'de saat eşitlenip bir kez
        yeniden denenir (borsa isteği reddetmiştir, yeniden göndermek güvenli)."""
        try:
            return self._send(method, path, params)
        except ExchangeError as error:
            if error.code != -1021:
                raise
        self.sync_time()
        return self._send(method, path, params)

    def _send(self, method: str, path: str, params: Mapping[str, Any] | None) -> Any:
        weight = ALLOWED.get((method, path))
        if weight is None:
            raise TradingRefused(f"İzin listesinde olmayan istek: {method} {path}")
        values = {key: value for key, value in (params or {}).items() if value is not None}
        values["recvWindow"] = RECV_WINDOW_MS
        stamp = self.now_ms()
        values["timestamp"] = stamp
        query = urllib.parse.urlencode(values)
        signature = urllib.parse.quote(sign(query, self._key.private_der_b64), safe="")
        url = f"{self.base}{path}?{query}&signature={signature}"
        self.budget.reserve(weight, path)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "X-MBX-APIKEY": self._key.api_key.reveal()},
            method=method,
        )
        changes_state = method in ("POST", "DELETE")
        try:
            with self.opener(request, timeout=self.timeout) as response:
                self._observe(dict(response.headers.items()))
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            failure = self._http_error(error, method, path)
            if error.headers:
                self._observe(dict(error.headers.items()))
            if changes_state and (error.code >= 500 or failure.code in UNKNOWN_CODES):
                raise OutcomeUnknown(method=method, path=path,
                                     detail=f"HTTP {error.code} {failure.msg}"[:200],
                                     sent_ms=stamp) from None
            # Adres imza içerir; hata metnine yalnızca yol yazılır.
            raise failure from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException,
                ValueError) as error:
            detail = f"{type(error).__name__}: {error}"[:200]
            if changes_state:
                raise OutcomeUnknown(method=method, path=path, detail=detail,
                                     sent_ms=stamp) from None
            raise ExchangeError(status=0, code=None, msg=detail, method=method,
                                path=path) from None

    # --- okuma -------------------------------------------------------------

    def account(self) -> dict[str, Any]:
        result = self.request("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        return result if isinstance(result, dict) else {}

    def commission(self, symbol: str) -> dict[str, Any]:
        result = self.request("GET", "/api/v3/account/commission", {"symbol": symbol})
        return result if isinstance(result, dict) else {}

    def query_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        result = self.request("GET", "/api/v3/order",
                              {"symbol": symbol,
                               "origClientOrderId": _require_prefix("origClientOrderId",
                                                                    client_id)})
        return result if isinstance(result, dict) else {}

    def query_order_list(self, list_client_id: str) -> dict[str, Any]:
        result = self.request("GET", "/api/v3/orderList",
                              {"origClientOrderId": _require_prefix("origClientOrderId",
                                                                    list_client_id)})
        return result if isinstance(result, dict) else {}

    def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        result = self.request("GET", "/api/v3/openOrders", {"symbol": symbol})
        return list(result) if isinstance(result, list) else []

    def my_trades(self, symbol: str, order_id: int) -> list[dict[str, Any]]:
        result = self.request("GET", "/api/v3/myTrades",
                              {"symbol": symbol, "orderId": int(order_id)})
        return list(result) if isinstance(result, list) else []

    # --- emir ----------------------------------------------------------------

    def place_otoco(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Giriş (``LIMIT_MAKER`` alış) + dolunca devreye giren hedef/stop çifti."""
        _require(params, "workingType", ("LIMIT_MAKER",))
        _require(params, "workingSide", ("BUY",))
        _require(params, "pendingSide", ("SELL",))
        _require(params, "pendingAboveType", ("LIMIT_MAKER",))
        _require(params, "pendingBelowType", ("STOP_LOSS", "STOP_LOSS_LIMIT"))
        for name in ("listClientOrderId", "workingClientOrderId", "pendingAboveClientOrderId",
                     "pendingBelowClientOrderId"):
            _require_prefix(name, params.get(name))
        payload = dict(params)
        payload["newOrderRespType"] = "FULL"
        result = self.request("POST", "/api/v3/orderList/otoco", payload)
        return result if isinstance(result, dict) else {}

    def place_oco(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Elde tutulan coin için hedef (``LIMIT_MAKER`` satış) + stop çifti."""
        _require(params, "side", ("SELL",))
        _require(params, "aboveType", ("LIMIT_MAKER",))
        _require(params, "belowType", ("STOP_LOSS", "STOP_LOSS_LIMIT"))
        for name in ("listClientOrderId", "aboveClientOrderId", "belowClientOrderId"):
            _require_prefix(name, params.get(name))
        payload = dict(params)
        payload["newOrderRespType"] = "FULL"
        result = self.request("POST", "/api/v3/orderList/oco", payload)
        return result if isinstance(result, dict) else {}

    def place_exit(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Korumalı çıkış: fiyat sınırlı, anında-ya-iptal (``LIMIT`` + ``IOC``) satış.

        Piyasa emri kullanılmaz: fiyat sınırı olmayan satış, ince bir defterde
        beklenmedik bir fiyattan dolabilir. Sınır, en iyi alışın azami kayma
        kadar altıdır; o fiyattan alıcı yoksa emir dolmadan biter ve yeniden
        denenir.
        """
        _require(params, "side", ("SELL",))
        _require(params, "type", ("LIMIT",))
        _require(params, "timeInForce", ("IOC",))
        _require_prefix("newClientOrderId", params.get("newClientOrderId"))
        payload = dict(params)
        payload["newOrderRespType"] = "FULL"
        result = self.request("POST", "/api/v3/order", payload)
        return result if isinstance(result, dict) else {}

    def cancel_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        result = self.request("DELETE", "/api/v3/order",
                              {"symbol": symbol,
                               "origClientOrderId": _require_prefix("origClientOrderId",
                                                                    client_id)})
        return result if isinstance(result, dict) else {}

    def cancel_order_list(self, symbol: str, list_client_id: str) -> dict[str, Any]:
        result = self.request("DELETE", "/api/v3/orderList",
                              {"symbol": symbol,
                               "listClientOrderId": _require_prefix("listClientOrderId",
                                                                    list_client_id)})
        return result if isinstance(result, dict) else {}


__all__ = [
    "ALLOWED",
    "CLIENT_PREFIX",
    "KEYCHAIN_SERVICE_DEMO",
    "DemoTrader",
    "ExchangeError",
    "OrderCounts",
    "OutcomeUnknown",
    "TradingRefused",
]
