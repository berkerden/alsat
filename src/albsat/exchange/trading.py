"""İmzalı Binance emir istemcileri: ``DemoTrader`` (Demo Mode, sahte para) ve
``LiveTrader`` (canlı hesap, **gerçek para**).

Faz 5'in borsaya dokunan tek sınıfı ``DemoTrader``'dı; Faz 6 aynı çekirdeği
(``SignedTrader``) paylaşan ayrı bir ``LiveTrader`` ekledi. İki sınıfın
ortamı, adresi, emir kimliği öneki ve izin listesi ayrıdır; biri ötekinin
yerine kurulamaz. Faz 4'ün salt okuyan ``SignedReader``'ı değiştirilmedi.
Hepsi listede olmayan bir adresi istek göndermeden reddeder.

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

**Ortam sınıfa bağlıdır.** ``DemoTrader`` yalnızca ``Environment.DEMO``,
``LiveTrader`` yalnızca ``Environment.LIVE`` ile kurulur; adresi kendisi
``endpoints.py``'den alır, dışarıdan adres verilemez.

**Yalnızca kendi emirleri.** Gönderilen ve iptal edilen her emrin kimliği
sınıfın önekiyle (``albsat-demo-`` ya da ``albsat-canli-``) başlamak
zorundadır. Kullanıcının elle verdiği emirler bu önekle başlamadığı için bu
sınıflar onları iptal edemez. Bütün açık emirleri tek seferde silen
``DELETE /api/v3/openOrders`` bilerek listede yok.

**Canlıda iki ek kilit (``LiveTrader``).**

1. *Anahtar izni.* Yeni giriş emri (OTOCO) ancak anahtarın izinleri son bir
   saat içinde ``/sapi/v1/account/apiRestrictions`` ile okunmuş ve para
   çekme izni **kapalı** bulunmuşsa gider. ``GET /api/v3/account``'taki
   ``canWithdraw`` hesabın bayrağıdır, anahtarın izni değildir; ona
   bakılmaz. Koruma emirleri (OCO, korumalı çıkış) ve iptaller bu kilide
   takılmaz: elde coin varken stop koymak ya da bekleyen alışı iptal etmek
   riski azaltır, engellenmemelidir.
2. *Emir tavanı.* Giriş emrinin tutarı (fiyat × miktar) yürütücünün verdiği
   tavanı aşarsa istek gönderilmez. Tavan risk motorunda da uygulanır; bu,
   aynı kuralın borsaya en yakın yerdeki ikinci kopyasıdır.

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
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from albsat.exchange.endpoints import Environment, endpoints_for
from albsat.exchange.http import USER_AGENT
from albsat.exchange.ratelimit import RequestBudget, parse_retry_after
from albsat.exchange.signed import RECV_WINDOW_MS, StoredKey, sign

#: Bu uygulamanın Demo Mode emirlerinin kimlik öneki.
DEMO_PREFIX = "albsat-demo-"
#: Bu uygulamanın canlı hesap (gerçek para) emirlerinin kimlik öneki.
LIVE_PREFIX = "albsat-canli-"
#: Faz 5 adı (Demo).
CLIENT_PREFIX = DEMO_PREFIX

#: Demo anahtarının Anahtar Zinciri kaydı. Faz 4'ün salt okuma anahtarından
#: (``albsat-binance``) ayrıdır; iki anahtar birbirinin yerine kullanılamaz.
KEYCHAIN_SERVICE_DEMO = "albsat-binance-demo"
#: Canlı işlem anahtarının Anahtar Zinciri kaydı (Faz 6). Salt okuma ve Demo
#: anahtarlarından ayrıdır.
KEYCHAIN_SERVICE_LIVE = "albsat-binance-canli"

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

#: Canlıda ek olarak anahtarın izinleri okunur (``/sapi/``, ağırlık 1).
PERMISSIONS_PATH = "/sapi/v1/account/apiRestrictions"
ALLOWED_LIVE: Mapping[tuple[str, str], int] = {**ALLOWED, ("GET", PERMISSIONS_PATH): 1}

#: Canlıda yeni giriş emri için anahtar izinlerinin en fazla bu kadar eski olması.
PERMISSION_MAX_AGE_SECONDS = 3600.0
#: Emir tavanı denetiminde yuvarlamaya bırakılan pay.
CAP_TOLERANCE = Decimal("1.01")

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


def _require_prefix(name: str, value: object, prefix: str = DEMO_PREFIX) -> str:
    text = str(value or "")
    if not text.startswith(prefix):
        raise TradingRefused(
            f"{name} '{text}' bu hesabın öneki ({prefix}) ile başlamıyor; "
            "başka birinin emrine dokunulmaz."
        )
    if len(text) > 36:
        raise TradingRefused(f"{name} 36 karakterden uzun.")
    return text


def _require(params: Mapping[str, Any], name: str, allowed: tuple[str, ...]) -> None:
    value = str(params.get(name, ""))
    if value not in allowed:
        raise TradingRefused(f"{name}={value!r} izinli değil (izinli: {', '.join(allowed)}).")


class SignedTrader:
    """İmzalı emir istemcisinin çekirdeği. Doğrudan kurulmaz; ``DemoTrader`` ya da
    ``LiveTrader`` kullanılır."""

    ENVIRONMENT: ClassVar[Environment]
    PREFIX: ClassVar[str]
    REAL_MONEY: ClassVar[bool]
    ALLOWED: ClassVar[Mapping[tuple[str, str], int]] = ALLOWED
    NAME: ClassVar[str] = "SignedTrader"

    def __init__(
        self,
        key: StoredKey,
        *,
        environment: Environment | None = None,
        budget: RequestBudget | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
        time_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        timeout: float = 15.0,
    ) -> None:
        expected = self.ENVIRONMENT
        environment = expected if environment is None else environment
        if environment is not expected:
            raise TradingRefused(
                f"{self.NAME} yalnızca '{expected.value}' ortamında kurulur "
                f"(istenen ortam: {environment.value})."
            )
        endpoints = endpoints_for(environment)
        if endpoints.real_money is not self.REAL_MONEY:  # pragma: no cover - endpoints.py
            raise TradingRefused(
                f"{self.NAME}: ortamın gerçek para bilgisi beklenenle uyuşmuyor; durduruldu.")
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
        return f"{self.NAME}({self.base}, <anahtar gizli>)"

    @property
    def prefix(self) -> str:
        return self.PREFIX

    @property
    def real_money(self) -> bool:
        return self.REAL_MONEY

    def _own(self, name: str, value: object) -> str:
        return _require_prefix(name, value, self.PREFIX)

    def _check_entry(self, params: Mapping[str, Any]) -> None:
        """Giriş emri gönderilmeden önceki ek denetim (canlıda izin ve tavan)."""

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
        weight = self.ALLOWED.get((method, path))
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
                               "origClientOrderId": self._own("origClientOrderId", client_id)})
        return result if isinstance(result, dict) else {}

    def query_order_list(self, list_client_id: str) -> dict[str, Any]:
        result = self.request("GET", "/api/v3/orderList",
                              {"origClientOrderId": self._own("origClientOrderId", list_client_id)})
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
            self._own(name, params.get(name))
        self._check_entry(params)
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
            self._own(name, params.get(name))
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
        self._own("newClientOrderId", params.get("newClientOrderId"))
        payload = dict(params)
        payload["newOrderRespType"] = "FULL"
        result = self.request("POST", "/api/v3/order", payload)
        return result if isinstance(result, dict) else {}

    def cancel_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        result = self.request("DELETE", "/api/v3/order",
                              {"symbol": symbol,
                               "origClientOrderId": self._own("origClientOrderId", client_id)})
        return result if isinstance(result, dict) else {}

    def cancel_order_list(self, symbol: str, list_client_id: str) -> dict[str, Any]:
        result = self.request("DELETE", "/api/v3/orderList",
                              {"symbol": symbol,
                               "listClientOrderId": self._own("listClientOrderId", list_client_id)})
        return result if isinstance(result, dict) else {}


class DemoTrader(SignedTrader):
    """Binance Demo Mode'a (sahte para) imzalı istek gönderen sınıf."""

    ENVIRONMENT = Environment.DEMO
    PREFIX = DEMO_PREFIX
    REAL_MONEY = False
    NAME = "DemoTrader"


@dataclass(frozen=True)
class PermissionState:
    """Canlı anahtarın son okunan izinleri (``apiRestrictions``)."""

    #: Okuma başarılı ve engelleyici sorun yok.
    tamam: bool
    #: Okunduğu an (tekdüze saat); hiç okunmadıysa ``None``.
    zaman: float | None
    engeller: tuple[str, ...] = ()
    uyarilar: tuple[str, ...] = ()
    #: IP kısıtlaması var mı (Binance'in bildirdiği ``ipRestrict``).
    ip_kisitli: bool | None = None
    #: Para çekme izni (``enableWithdrawals``); ``canWithdraw`` DEĞİL.
    cekim_izni: bool | None = None
    #: Spot işlem izni (``enableSpotAndMarginTrading``).
    islem_izni: bool | None = None
    hata: str | None = None


class LiveTrader(SignedTrader):
    """Binance **canlı** hesabına (gerçek para) imzalı istek gönderen tek sınıf.

    ``DemoTrader``'dan farkları: ortam ``LIVE``, kimlik öneki ``albsat-canli-``,
    izin listesinde anahtarın izinlerini okuyan ``/sapi/v1/account/apiRestrictions``
    var, ve iki ek kilit: yeni giriş emri ancak izinler taze ve para çekme kapalı
    okunmuşsa, tutarı da tavanı aşmıyorsa gider.
    """

    ENVIRONMENT = Environment.LIVE
    PREFIX = LIVE_PREFIX
    REAL_MONEY = True
    ALLOWED = ALLOWED_LIVE
    NAME = "LiveTrader"

    def __init__(
        self,
        key: StoredKey,
        *,
        environment: Environment | None = None,
        budget: RequestBudget | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
        time_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        timeout: float = 15.0,
        entry_cap_usdt: Callable[[], Decimal | None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(key, environment=environment, budget=budget, opener=opener,
                         time_ms=time_ms, timeout=timeout)
        self.entry_cap_usdt = entry_cap_usdt
        self.monotonic = monotonic
        self._permissions = PermissionState(tamam=False, zaman=None)

    # --- izinler -----------------------------------------------------------------

    def permissions(self) -> PermissionState:
        with self._lock:
            return self._permissions

    def api_restrictions(self) -> dict[str, Any]:
        result = self.request("GET", PERMISSIONS_PATH)
        return result if isinstance(result, dict) else {}

    def verify_permissions(self) -> PermissionState:
        """Anahtarın izinlerini okur. Okunamazsa önceki durum korunur, hata yazılır
        (izin tazeliği dolunca yeni giriş yine kapanır)."""
        from albsat.exchange.signed import live_key_problems

        try:
            payload = self.api_restrictions()
        except Exception as error:  # noqa: BLE001 - çağıran taraf hatayı gösterir
            with self._lock:
                previous = self._permissions
                self._permissions = PermissionState(
                    tamam=previous.tamam, zaman=previous.zaman, engeller=previous.engeller,
                    uyarilar=previous.uyarilar, ip_kisitli=previous.ip_kisitli,
                    cekim_izni=previous.cekim_izni, islem_izni=previous.islem_izni,
                    hata=f"{type(error).__name__}: {error}"[:300],
                )
            raise
        blocking, warnings = live_key_problems(payload)
        state = PermissionState(
            tamam=not blocking,
            zaman=self.monotonic(),
            engeller=tuple(blocking),
            uyarilar=tuple(warnings),
            ip_kisitli=bool(payload.get("ipRestrict")),
            cekim_izni=bool(payload.get("enableWithdrawals")),
            islem_izni=bool(payload.get("enableSpotAndMarginTrading")),
        )
        with self._lock:
            self._permissions = state
        return state

    def entry_block_reason(self) -> str | None:
        """Yeni giriş emri neden gönderilemez? ``None``: gönderilebilir."""
        state = self.permissions()
        if state.zaman is None:
            return "Canlı anahtarın izinleri henüz okunmadı."
        if not state.tamam:
            return "Canlı anahtar izinleri uygun değil: " + " ".join(state.engeller)
        if self.monotonic() - state.zaman > PERMISSION_MAX_AGE_SECONDS:
            return "Canlı anahtarın izinleri bir saatten uzun süredir okunamadı."
        return None

    def _check_entry(self, params: Mapping[str, Any]) -> None:
        reason = self.entry_block_reason()
        if reason is not None:
            raise TradingRefused(reason + " Giriş emri gönderilmedi.")
        cap = self.entry_cap_usdt() if self.entry_cap_usdt is not None else None
        if cap is None:
            return
        try:
            notional = Decimal(str(params["workingPrice"])) * \
                Decimal(str(params["workingQuantity"]))
        except (KeyError, InvalidOperation, ValueError):
            raise TradingRefused("Giriş emrinin tutarı okunamadı; gönderilmedi.") from None
        if notional > cap * CAP_TOLERANCE:
            raise TradingRefused(
                f"Giriş emrinin tutarı ({notional:.2f} USDT) canlı emir tavanını "
                f"({cap} USDT) aşıyor; gönderilmedi.")


__all__ = [
    "ALLOWED",
    "ALLOWED_LIVE",
    "CLIENT_PREFIX",
    "DEMO_PREFIX",
    "KEYCHAIN_SERVICE_DEMO",
    "KEYCHAIN_SERVICE_LIVE",
    "LIVE_PREFIX",
    "PERMISSIONS_PATH",
    "PERMISSION_MAX_AGE_SECONDS",
    "DemoTrader",
    "LiveTrader",
    "PermissionState",
    "SignedTrader",
    "ExchangeError",
    "OrderCounts",
    "OutcomeUnknown",
    "TradingRefused",
]
