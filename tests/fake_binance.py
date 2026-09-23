"""Kaos testleri için sahte Binance Demo Mode.

Gerçek borsanın bu fazda kullanılan kısmını taklit eder:

* **İmzalı REST.** Her istekte ``X-MBX-APIKEY`` ve Ed25519 imzası gerçekten
  doğrulanır; ``timestamp`` sunucu saatine göre ``recvWindow`` dışındaysa
  ``-1021`` döner. Yani testler istemcinin gerçek istek biçimini sınar.
* **Eşleştirme.** ``LIMIT_MAKER`` (hemen eşleşecekse ret), ``STOP_LOSS`` ve
  ``STOP_LOSS_LIMIT`` (hemen tetiklenecekse ret), ``LIMIT IOC``; OCO'da bir
  bacak dolunca (kısmen de olsa) öteki ``EXPIRED``; OTOCO'da giriş tamamen
  dolunca hedef/stop devreye girer. Bakiye kilitlenir ve serbest bırakılır;
  komisyon alınan varlıktan kesilir.
* **Hesap akışı.** Her değişiklik ``executionReport``, ``listStatus`` ve
  ``outboundAccountPosition`` olarak yayınlanır. Akış "kopuk"ken olaylar
  kaybolur (kaos).
* **Hata enjeksiyonu.** Bir sonraki eşleşen isteğe: yanıt gelmeden zaman aşımı
  (borsa işlemedi), işledikten sonra zaman aşımı, 5xx, ``-1007``, 429, 418,
  ``-1021``.
"""

from __future__ import annotations

import base64
import email.message
import io
import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from cryptography.hazmat.primitives.serialization import load_pem_public_key

D = Decimal
ZERO = D("0")
FEE = D("0.001")
OPEN = ("NEW", "PARTIALLY_FILLED")
FINAL = ("FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH")

SYMBOLS: dict[str, dict[str, Any]] = {
    "BTCUSDT": {"base": "BTC", "tick": D("0.01"), "step": D("0.00001"), "min_qty": D("0.00001")},
    "SOLUSDT": {"base": "SOL", "tick": D("0.01"), "step": D("0.001"), "min_qty": D("0.001")},
}
MIN_NOTIONAL = D("5")
START_USDT = D("5000")


def exchange_info(symbols: list[str] | None = None) -> dict[str, Any]:
    names = symbols or list(SYMBOLS)
    return {
        "timezone": "UTC",
        "serverTime": 0,
        "rateLimits": [
            {"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1,
             "limit": 6000},
            {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 100},
            {"rateLimitType": "ORDERS", "interval": "DAY", "intervalNum": 1, "limit": 200000},
        ],
        "symbols": [
            {
                "symbol": name, "status": "TRADING", "baseAsset": SYMBOLS[name]["base"],
                "quoteAsset": "USDT", "ocoAllowed": True, "otoAllowed": True,
                "isSpotTradingAllowed": True,
                "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS",
                               "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"],
                "filters": [
                    {"filterType": "PRICE_FILTER", "minPrice": "0.01000000",
                     "maxPrice": "1000000.00000000",
                     "tickSize": format(SYMBOLS[name]["tick"], "f")},
                    {"filterType": "LOT_SIZE", "minQty": format(SYMBOLS[name]["min_qty"], "f"),
                     "maxQty": "9000.00000000", "stepSize": format(SYMBOLS[name]["step"], "f")},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000",
                     "applyMinToMarket": True, "maxNotional": "9000000.00000000",
                     "applyMaxToMarket": False, "avgPriceMins": 5},
                ],
            }
            for name in names
        ],
    }


def _s(value: Decimal) -> str:
    return format(value.quantize(D("0.00000001")), "f")


@dataclass
class Lock:
    asset: str
    amount: Decimal


@dataclass
class Order:
    symbol: str
    order_id: int
    client_id: str
    side: str
    type: str
    price: Decimal
    stop_price: Decimal
    qty: Decimal
    status: str
    tif: str
    time: int
    list_id: int = -1
    executed: Decimal = ZERO
    quote: Decimal = ZERO
    update: int = 0
    lock: Lock | None = None
    triggered: bool = False
    expiry_reason: str | None = None

    @property
    def remaining(self) -> Decimal:
        return self.qty - self.executed

    @property
    def is_open(self) -> bool:
        return self.status in OPEN

    def report(self, *, now: int, orig: bool = False, cancel_id: str | None = None
               ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "symbol": self.symbol, "orderId": self.order_id, "orderListId": self.list_id,
            "clientOrderId": cancel_id or self.client_id, "transactTime": now,
            "price": _s(self.price), "origQty": _s(self.qty), "executedQty": _s(self.executed),
            "cummulativeQuoteQty": _s(self.quote), "status": self.status,
            "timeInForce": self.tif, "type": self.type, "side": self.side,
            "workingTime": self.time, "selfTradePreventionMode": "NONE",
        }
        if orig:
            data["origClientOrderId"] = self.client_id
        if self.type.startswith("STOP"):
            data["stopPrice"] = _s(self.stop_price)
        if self.expiry_reason:
            data["expiryReason"] = self.expiry_reason
        return data

    def query(self) -> dict[str, Any]:
        data = self.report(now=self.update)
        data.pop("transactTime")
        data.update({"time": self.time, "updateTime": self.update or self.time,
                     "isWorking": self.status != "PENDING_NEW", "icebergQty": "0.00000000",
                     "origQuoteOrderQty": "0.00000000"})
        return data


@dataclass
class OrderList:
    list_id: int
    client_id: str
    symbol: str
    contingency: str
    order_ids: list[int]
    working_id: int | None = None
    status_type: str = "EXEC_STARTED"
    order_status: str = "EXECUTING"


@dataclass
class Fault:
    method: str
    path: str
    kind: str
    times: int = 1


@dataclass
class Response:
    body: bytes
    headers: dict[str, str]

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class ApiFailure(Exception):
    def __init__(self, status: int, code: int, msg: str,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(msg)
        self.status = status
        self.code = code
        self.msg = msg
        self.headers = headers or {}


class FakeBinance:
    """Tek hesaplı, tek süreçli sahte borsa (Demo Mode ya da canlı; aynı kurallar)."""

    def __init__(self, *, clock_ms: Callable[[], int], public_pem: str, api_key: str,
                 usdt: Decimal = START_USDT) -> None:
        self.clock_ms = clock_ms
        self.public_key = load_pem_public_key(public_pem.encode())
        self.api_key = api_key
        self.balances: dict[str, list[Decimal]] = {"USDT": [usdt, ZERO], "BTC": [ZERO, ZERO],
                                                   "SOL": [ZERO, ZERO]}
        self.book: dict[str, tuple[Decimal, Decimal]] = {
            "BTCUSDT": (D("60049.99"), D("60050.00")), "SOLUSDT": (D("149.99"), D("150.00"))}
        #: IOC satışın bulabileceği en fazla miktar (``None``: sınırsız).
        self.depth: dict[str, Decimal | None] = {}
        self.orders: dict[int, Order] = {}
        self.by_client: dict[str, int] = {}
        self.lists: dict[int, OrderList] = {}
        self.list_by_client: dict[str, int] = {}
        self.trades: list[dict[str, Any]] = []
        self.faults: list[Fault] = []
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.listeners: list[Callable[[str], None]] = []
        self.book_listeners: list[Callable[[str, Decimal, Decimal], None]] = []
        self.stream_up = True
        self.lost_events = 0
        self.can_withdraw = False
        self.can_trade = True
        #: Anahtarın izinleri (``/sapi/v1/account/apiRestrictions``; yalnızca canlı).
        self.restrictions: dict[str, Any] = {
            "ipRestrict": False, "createTime": 1_700_000_000_000, "enableReading": True,
            "enableSpotAndMarginTrading": True, "enableWithdrawals": False,
            "enableInternalTransfer": False, "enableMargin": False, "enableFutures": False,
            "permitsUniversalTransfer": False, "enableVanillaOptions": False,
            "enablePortfolioMarginTrading": False, "enableFixApiTrade": False,
            "enableFixReadOnly": False,
        }
        #: OTOCO'nun bekleyen bacakları sorguda görünsün mü (belgede açık değil).
        self.pending_queryable = True
        self.order_count_10s = 0
        self.order_count_1d = 0
        self._next_order = 1000
        self._next_list = 500
        self._next_trade = 90000

    # --- test yardımcıları ---------------------------------------------------------

    def fail_next(self, method: str, path: str, kind: str, times: int = 1) -> None:
        self.faults.append(Fault(method, path, kind, times))

    def set_book(self, symbol: str, bid: Decimal | str, ask: Decimal | str, *,
                 publish: bool = True) -> None:
        self.book[symbol] = (D(bid), D(ask))
        if publish:
            for listener in list(self.book_listeners):
                listener(symbol, D(bid), D(ask))

    def move(self, symbol: str, price: Decimal | str, qty: Decimal | str | None = None, *,
             publish_book: bool = True) -> None:
        """Piyasada ``price``'tan işlem olur; ``qty`` kadar karşı taraf vardır."""
        price = D(price)
        tick = SYMBOLS[symbol]["tick"]
        self.set_book(symbol, price - tick, price, publish=publish_book)
        self._match(symbol, price, None if qty is None else D(qty))

    def orders_by_prefix(self, prefix: str = "albsat-demo-") -> list[Order]:
        return [order for order in self.orders.values() if order.client_id.startswith(prefix)]

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        return [order for order in self.orders.values()
                if (order.is_open or order.status == "PENDING_NEW")
                and (symbol is None or order.symbol == symbol)]

    def order(self, client_id: str) -> Order:
        return self.orders[self.by_client[client_id]]

    def posts(self, path: str | None = None) -> list[dict[str, str]]:
        return [params for method, req_path, params in self.requests
                if method == "POST" and (path is None or req_path == path)]

    def add_manual_order(self, symbol: str, client_id: str, side: str, price: str,
                         qty: str) -> Order:
        """Kullanıcının Binance arayüzünden verdiği emir (albsat öneki yok)."""
        order = self._new_order(symbol, client_id, side, "LIMIT", D(price), ZERO, D(qty), "GTC")
        self._lock_for(order)
        order.status = "NEW"
        return order

    def expire(self, client_id: str, reason: str = "STOP_PRICE_OUT_OF_RANGE") -> None:
        """Borsanın bir emri kendiliğinden bitirmesi (ör. fiyat aralığı kuralı)."""
        order = self.order(client_id)
        order.expiry_reason = reason
        self._finish(order, "EXPIRED")
        self._event(order, "EXPIRED")
        self._after_change(order)

    # --- ağ katmanı ------------------------------------------------------------------

    def __call__(self, request: Any, timeout: float | None = None) -> Response:
        method = request.get_method()
        parts = urllib.parse.urlsplit(request.full_url)
        path = parts.path
        query = parts.query
        fault = self._take_fault(method, path)
        try:
            if fault is not None and fault.kind == "kopuk_once":
                raise TimeoutError("timed out")
            if fault is not None and fault.kind in ("429", "418"):
                status = int(fault.kind)
                retry = "2" if status == 429 else "120"
                raise ApiFailure(status, -1003, "Too much request weight used.",
                                 {"Retry-After": retry})
            if fault is not None and fault.kind == "500_once":
                raise ApiFailure(503, -1001, "Internal error; unable to process your request.")
            if path == "/api/v3/time":
                return self._respond({"serverTime": self.clock_ms()})
            params = self._authenticate(request, query, fault)
            self.requests.append((method, path, params))
            result = self._dispatch(method, path, params)
            if fault is not None and fault.kind == "kopuk_sonra":
                raise TimeoutError("timed out")
            if fault is not None and fault.kind == "500_sonra":
                raise ApiFailure(502, -1001, "Internal error.")
            if fault is not None and fault.kind == "-1007_sonra":
                raise ApiFailure(503, -1007, "Timeout waiting for response from backend "
                                 "server. Send status unknown; execution status unknown.")
            return self._respond(result, order=path.startswith("/api/v3/order")
                                 and method == "POST")
        except ApiFailure as failure:
            headers = email.message.Message()
            for key, value in failure.headers.items():
                headers[key] = value
            body = json.dumps({"code": failure.code, "msg": failure.msg}).encode()
            raise urllib.error.HTTPError(request.full_url, failure.status, failure.msg,
                                         headers, io.BytesIO(body)) from None

    def _take_fault(self, method: str, path: str) -> Fault | None:
        for fault in self.faults:
            if fault.times > 0 and fault.method == method and fault.path == path:
                fault.times -= 1
                return fault
        return None

    def _respond(self, payload: Any, *, order: bool = False) -> Response:
        headers = {"X-MBX-USED-WEIGHT-1M": "10"}
        if order:
            self.order_count_10s += 1
            self.order_count_1d += 1
        headers["X-MBX-ORDER-COUNT-10S"] = str(self.order_count_10s)
        headers["X-MBX-ORDER-COUNT-1D"] = str(self.order_count_1d)
        return Response(json.dumps(payload).encode(), headers)

    def _authenticate(self, request: Any, query: str, fault: Fault | None) -> dict[str, str]:
        if request.get_header("X-mbx-apikey") != self.api_key:
            raise ApiFailure(401, -2015, "Invalid API-key, IP, or permissions for action.")
        signed, marker, signature = query.rpartition("&signature=")
        if not marker:
            raise ApiFailure(400, -1102, "Mandatory parameter 'signature' was not sent.")
        try:
            raw = base64.b64decode(urllib.parse.unquote(signature))
            self.public_key.verify(raw, signed.encode())  # type: ignore[call-arg, union-attr]
        except Exception:  # noqa: BLE001 - imza hatası
            raise ApiFailure(400, -1022, "Signature for this request is not valid.") from None
        params = dict(urllib.parse.parse_qsl(signed, keep_blank_values=True))
        stamp = int(params["timestamp"])
        window = int(params.get("recvWindow", 5000))
        now = self.clock_ms()
        if fault is not None and fault.kind == "-1021" or stamp > now + 1000 or \
                now - stamp > window:
            raise ApiFailure(400, -1021, "Timestamp for this request is outside of the "
                             "recvWindow.")
        return params

    # --- uçlar -------------------------------------------------------------------------

    def _dispatch(self, method: str, path: str, params: dict[str, str]) -> Any:
        key = (method, path)
        if key == ("GET", "/api/v3/account"):
            return {
                "canTrade": self.can_trade, "canWithdraw": self.can_withdraw,
                "canDeposit": True, "accountType": "SPOT",
                "balances": [{"asset": asset, "free": _s(free), "locked": _s(locked)}
                             for asset, (free, locked) in self.balances.items()
                             if free or locked],
            }
        if key == ("GET", "/sapi/v1/account/apiRestrictions"):
            return dict(self.restrictions)
        if key == ("GET", "/api/v3/account/commission"):
            rates = {"maker": _s(FEE), "taker": _s(FEE), "buyer": "0.00000000",
                     "seller": "0.00000000"}
            zero = {"maker": "0", "taker": "0", "buyer": "0", "seller": "0"}
            return {"symbol": params["symbol"], "standardCommission": rates,
                    "specialCommission": zero, "taxCommission": zero,
                    "discount": {"enabledForAccount": False, "enabledForSymbol": False,
                                 "discountAsset": "BNB", "discount": "0.75000000"}}
        if key == ("GET", "/api/v3/order"):
            order = self._find(params["origClientOrderId"], params.get("symbol"))
            if order.status == "PENDING_NEW" and not self.pending_queryable:
                raise ApiFailure(400, -2013, "Order does not exist.")
            return order.query()
        if key == ("GET", "/api/v3/openOrders"):
            return [order.query() for order in self.open_orders(params["symbol"])
                    if order.status != "PENDING_NEW" or self.pending_queryable]
        if key == ("GET", "/api/v3/myTrades"):
            return [trade for trade in self.trades if trade["orderId"] == int(params["orderId"])
                    and trade["symbol"] == params["symbol"]]
        if key == ("POST", "/api/v3/orderList/otoco"):
            return self._place_otoco(params)
        if key == ("POST", "/api/v3/orderList/oco"):
            return self._place_oco(params)
        if key == ("POST", "/api/v3/order"):
            return self._place_ioc(params)
        if key == ("DELETE", "/api/v3/order"):
            return self._cancel_order(params)
        if key == ("DELETE", "/api/v3/orderList"):
            return self._cancel_list(params)
        raise ApiFailure(404, -1000, f"Sahte borsada yok: {method} {path}")

    def _find(self, client_id: str, symbol: str | None) -> Order:
        order_id = self.by_client.get(client_id)
        if order_id is None or (symbol and self.orders[order_id].symbol != symbol):
            raise ApiFailure(400, -2013, "Order does not exist.")
        return self.orders[order_id]

    # --- doğrulama ------------------------------------------------------------------------

    def _check_filters(self, symbol: str, price: Decimal, qty: Decimal) -> None:
        spec = SYMBOLS[symbol]
        if price <= 0 or price % spec["tick"] != 0:
            raise ApiFailure(400, -1013, "Filter failure: PRICE_FILTER")
        if qty < spec["min_qty"] or qty % spec["step"] != 0:
            raise ApiFailure(400, -1013, "Filter failure: LOT_SIZE")
        if price * qty < MIN_NOTIONAL:
            raise ApiFailure(400, -1013, "Filter failure: NOTIONAL")

    def _check_ids(self, *ids: str) -> None:
        for client in ids:
            if client in self.by_client or client in self.list_by_client:
                raise ApiFailure(400, -2010, "Duplicate order sent.")

    def _check_maker(self, symbol: str, side: str, price: Decimal) -> None:
        bid, ask = self.book[symbol]
        if (side == "BUY" and price >= ask) or (side == "SELL" and price <= bid):
            raise ApiFailure(400, -2010, "Order would immediately match and take.")

    def _check_stop(self, symbol: str, stop: Decimal) -> None:
        bid, _ = self.book[symbol]
        if stop >= bid:
            raise ApiFailure(400, -2010, "Order would trigger immediately.")

    def _free(self, asset: str) -> Decimal:
        return self.balances.setdefault(asset, [ZERO, ZERO])[0]

    # --- emir kurma -----------------------------------------------------------------------

    def _new_order(self, symbol: str, client_id: str, side: str, kind: str, price: Decimal,
                   stop: Decimal, qty: Decimal, tif: str, list_id: int = -1) -> Order:
        self._next_order += 1
        now = self.clock_ms()
        order = Order(symbol=symbol, order_id=self._next_order, client_id=client_id, side=side,
                      type=kind, price=price, stop_price=stop, qty=qty, status="NEW", tif=tif,
                      time=now, update=now, list_id=list_id)
        self.orders[order.order_id] = order
        self.by_client[client_id] = order.order_id
        return order

    def _lock_for(self, order: Order, lock: Lock | None = None) -> Lock:
        if lock is None:
            if order.side == "BUY":
                lock = Lock("USDT", order.price * order.qty)
            else:
                lock = Lock(SYMBOLS[order.symbol]["base"], order.qty)
            balance = self.balances.setdefault(lock.asset, [ZERO, ZERO])
            if balance[0] < lock.amount:
                raise ApiFailure(400, -2010,
                                 "Account has insufficient balance for requested action.")
            balance[0] -= lock.amount
            balance[1] += lock.amount
        order.lock = lock
        return lock

    def _stop_leg(self, symbol: str, prefix: str, params: dict[str, str], qty: Decimal,
                  list_id: int, client_key: str) -> Order:
        kind = params[f"{prefix}Type"]
        stop = D(params[f"{prefix}StopPrice"])
        limit = D(params.get(f"{prefix}Price", "0"))
        tif = params.get(f"{prefix}TimeInForce", "GTC") if kind == "STOP_LOSS_LIMIT" else "GTC"
        self._check_filters(symbol, limit if kind == "STOP_LOSS_LIMIT" else stop, qty)
        if kind == "STOP_LOSS_LIMIT":
            self._check_filters(symbol, stop, qty)
        return self._new_order(symbol, params[client_key], "SELL", kind, limit, stop, qty, tif,
                               list_id)

    def _list_json(self, item: OrderList, *, cancel: bool = False) -> dict[str, Any]:
        now = self.clock_ms()
        orders = [self.orders[order_id] for order_id in item.order_ids]
        return {
            "orderListId": item.list_id, "contingencyType": item.contingency,
            "listStatusType": item.status_type, "listOrderStatus": item.order_status,
            "listClientOrderId": item.client_id, "transactionTime": now, "symbol": item.symbol,
            "orders": [{"symbol": o.symbol, "orderId": o.order_id, "clientOrderId": o.client_id}
                       for o in orders],
            "orderReports": [o.report(now=now, orig=cancel,
                                      cancel_id=f"iptal{o.order_id}" if cancel else None)
                             for o in orders],
        }

    def _place_otoco(self, params: dict[str, str]) -> dict[str, Any]:
        symbol = params["symbol"]
        self._check_ids(params["listClientOrderId"], params["workingClientOrderId"],
                        params["pendingAboveClientOrderId"], params["pendingBelowClientOrderId"])
        entry = D(params["workingPrice"])
        qty = D(params["workingQuantity"])
        pending = D(params["pendingQuantity"])
        target = D(params["pendingAbovePrice"])
        stop = D(params["pendingBelowStopPrice"])
        self._check_filters(symbol, entry, qty)
        self._check_filters(symbol, target, pending)
        if not target > stop:
            raise ApiFailure(400, -2010, "The relationship of the prices for the orders is not "
                             "correct.")
        self._check_maker(symbol, "BUY", entry)
        if self._free("USDT") < entry * qty:
            raise ApiFailure(400, -2010, "Account has insufficient balance for requested action.")
        self._next_list += 1
        list_id = self._next_list
        working = self._new_order(symbol, params["workingClientOrderId"], "BUY", "LIMIT_MAKER",
                                  entry, ZERO, qty, "GTC", list_id)
        self._lock_for(working)
        above = self._new_order(symbol, params["pendingAboveClientOrderId"], "SELL",
                                "LIMIT_MAKER", target, ZERO, pending, "GTC", list_id)
        below = self._stop_leg(symbol, "pendingBelow", params, pending, list_id,
                               "pendingBelowClientOrderId")
        above.status = below.status = "PENDING_NEW"
        item = OrderList(list_id, params["listClientOrderId"], symbol, "OTO",
                         [working.order_id, above.order_id, below.order_id], working.order_id)
        self.lists[list_id] = item
        self.list_by_client[item.client_id] = list_id
        for order in (working, above, below):
            self._event(order, "NEW")
        self._list_event(item)
        self._balance_event("USDT")
        return self._list_json(item)

    def _place_oco(self, params: dict[str, str]) -> dict[str, Any]:
        symbol = params["symbol"]
        self._check_ids(params["listClientOrderId"], params["aboveClientOrderId"],
                        params["belowClientOrderId"])
        qty = D(params["quantity"])
        target = D(params["abovePrice"])
        stop = D(params["belowStopPrice"])
        bid, _ = self.book[symbol]
        self._check_filters(symbol, target, qty)
        if not target > bid > stop:
            raise ApiFailure(400, -2010, "The relationship of the prices for the orders is not "
                             "correct.")
        self._check_maker(symbol, "SELL", target)
        base = SYMBOLS[symbol]["base"]
        if self._free(base) < qty:
            raise ApiFailure(400, -2010, "Account has insufficient balance for requested action.")
        self._next_list += 1
        list_id = self._next_list
        above = self._new_order(symbol, params["aboveClientOrderId"], "SELL", "LIMIT_MAKER",
                                target, ZERO, qty, "GTC", list_id)
        below = self._stop_leg(symbol, "below", params, qty, list_id, "belowClientOrderId")
        lock = self._lock_for(above)
        self._lock_for(below, lock)
        item = OrderList(list_id, params["listClientOrderId"], symbol, "OCO",
                         [above.order_id, below.order_id])
        self.lists[list_id] = item
        self.list_by_client[item.client_id] = list_id
        for order in (above, below):
            self._event(order, "NEW")
        self._list_event(item)
        self._balance_event(base)
        return self._list_json(item)

    def _place_ioc(self, params: dict[str, str]) -> dict[str, Any]:
        symbol = params["symbol"]
        self._check_ids(params["newClientOrderId"])
        price = D(params["price"])
        qty = D(params["quantity"])
        self._check_filters(symbol, price, qty)
        base = SYMBOLS[symbol]["base"]
        if self._free(base) < qty:
            raise ApiFailure(400, -2010, "Account has insufficient balance for requested action.")
        order = self._new_order(symbol, params["newClientOrderId"], "SELL", "LIMIT", price, ZERO,
                                qty, "IOC")
        self._lock_for(order)
        self._event(order, "NEW")
        bid, _ = self.book[symbol]
        fills: list[dict[str, Any]] = []
        if bid >= price:
            depth = self.depth.get(symbol)
            amount = qty if depth is None else min(qty, depth)
            if amount > 0:
                if depth is not None:
                    self.depth[symbol] = depth - amount
                fills.append(self._fill(order, bid, amount, maker=False))
        if order.remaining > 0:
            self._finish(order, "EXPIRED")
            self._event(order, "EXPIRED")
        self._balance_event(base, "USDT")
        data = order.report(now=self.clock_ms())
        data["fills"] = [{"price": _s(item["price_d"]), "qty": _s(item["qty_d"]),
                          "commission": item["commission"],
                          "commissionAsset": item["commissionAsset"], "tradeId": item["id"]}
                         for item in fills]
        return data

    # --- iptal ---------------------------------------------------------------------------

    def _cancel_order(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            order = self._find(params["origClientOrderId"], params["symbol"])
        except ApiFailure:
            raise ApiFailure(400, -2011, "Unknown order sent.") from None
        if order.status in FINAL:
            raise ApiFailure(400, -2011, "Unknown order sent.")
        if order.list_id >= 0:
            return self._cancel_list({"symbol": order.symbol,
                                      "listClientOrderId": self.lists[order.list_id].client_id})
        self._finish(order, "CANCELED")
        self._event(order, "CANCELED", cancel_id=f"iptal{order.order_id}")
        self._balance_event(order.lock.asset if order.lock else "USDT")
        return order.report(now=self.clock_ms(), orig=True, cancel_id=f"iptal{order.order_id}")

    def _cancel_list(self, params: dict[str, str]) -> dict[str, Any]:
        list_id = self.list_by_client.get(params["listClientOrderId"])
        if list_id is None:
            raise ApiFailure(400, -2011, "Unknown order sent.")
        item = self.lists[list_id]
        live = [self.orders[order_id] for order_id in item.order_ids
                if self.orders[order_id].status not in FINAL]
        if not live:
            raise ApiFailure(400, -2011, "Unknown order sent.")
        assets: set[str] = set()
        for order in live:
            self._finish(order, "CANCELED")
            self._event(order, "CANCELED", cancel_id=f"iptal{order.order_id}")
            if order.lock is not None:
                assets.add(order.lock.asset)
        item.status_type = item.order_status = "ALL_DONE"
        self._list_event(item)
        self._balance_event(*assets)
        return self._list_json(item, cancel=True)

    # --- eşleştirme ---------------------------------------------------------------------

    def _fill(self, order: Order, price: Decimal, qty: Decimal, *, maker: bool) -> dict[str, Any]:
        base = SYMBOLS[order.symbol]["base"]
        quote_qty = price * qty
        if order.side == "BUY":
            commission = qty * FEE
            asset = base
            assert order.lock is not None
            released = order.price * qty
            order.lock.amount -= released
            self.balances["USDT"][1] -= released
            self.balances["USDT"][0] += released - quote_qty
            self.balances[base][0] += qty - commission
        else:
            commission = quote_qty * FEE
            asset = "USDT"
            assert order.lock is not None
            order.lock.amount -= qty
            self.balances[base][1] -= qty
            self.balances["USDT"][0] += quote_qty - commission
        order.executed += qty
        order.quote += quote_qty
        order.update = self.clock_ms()
        order.status = "FILLED" if order.remaining == 0 else "PARTIALLY_FILLED"
        self._next_trade += 1
        trade = {
            "symbol": order.symbol, "id": self._next_trade, "orderId": order.order_id,
            "orderListId": order.list_id, "price": _s(price), "qty": _s(qty),
            "quoteQty": _s(quote_qty), "commission": _s(commission), "commissionAsset": asset,
            "time": self.clock_ms(), "isBuyer": order.side == "BUY", "isMaker": maker,
            "isBestMatch": True,
        }
        self.trades.append(trade)
        self._event(order, "TRADE", trade=trade)
        if order.status == "FILLED":
            self._release(order)
        return {**trade, "price_d": price, "qty_d": qty}

    def _finish(self, order: Order, status: str) -> None:
        order.status = status
        order.update = self.clock_ms()
        self._release(order)

    def _release(self, order: Order) -> None:
        lock = order.lock
        if lock is None or lock.amount <= 0:
            return
        sharing = [item for item in self.orders.values() if item.lock is lock]
        if any(item.status not in FINAL for item in sharing):
            return
        balance = self.balances[lock.asset]
        balance[0] += lock.amount
        balance[1] -= lock.amount
        lock.amount = ZERO

    def _match(self, symbol: str, price: Decimal, liquidity: Decimal | None) -> None:
        for order in sorted(self.orders.values(), key=lambda item: item.order_id):
            if order.symbol != symbol or not order.is_open:
                continue
            if liquidity is not None and liquidity <= 0:
                break
            amount: Decimal | None = None
            fill_price = order.price
            maker = True
            resting = order.type in ("LIMIT", "LIMIT_MAKER")
            crossed = price <= order.price if order.side == "BUY" else price >= order.price
            if resting and crossed:
                amount = order.remaining
            elif order.side == "SELL" and order.type == "STOP_LOSS" and price <= order.stop_price:
                amount, fill_price, maker = order.remaining, price, False
                order.triggered = True
            elif order.side == "SELL" and order.type == "STOP_LOSS_LIMIT":
                if not order.triggered and price <= order.stop_price:
                    order.triggered = True
                if order.triggered and price >= order.price:
                    amount, fill_price, maker = order.remaining, price, False
            if amount is None or amount <= 0:
                continue
            if liquidity is not None:
                amount = min(amount, liquidity)
                liquidity -= amount
            self._fill(order, fill_price, amount, maker=maker)
            if order.type == "STOP_LOSS" and order.remaining > 0:
                # Piyasa stopu: karşı taraf bitince kalan süresi dolar.
                self._finish(order, "EXPIRED")
                self._event(order, "EXPIRED")
            self._after_change(order)

    def _after_change(self, order: Order) -> None:
        """OCO'da kardeşi bitir, OTOCO'da bekleyenleri devreye sok, hesabı yayınla."""
        assets = {"USDT", SYMBOLS[order.symbol]["base"]}
        if order.list_id < 0:
            self._balance_event(*assets)
            return
        item = self.lists[order.list_id]
        members = [self.orders[order_id] for order_id in item.order_ids]
        if item.contingency == "OTO" and order.order_id == item.working_id:
            if order.status == "FILLED":
                self._activate(item, members)
            elif order.status in ("CANCELED", "EXPIRED"):
                for other in members:
                    if other is not order and other.status == "PENDING_NEW":
                        self._finish(other, "EXPIRED")
                        self._event(other, "EXPIRED")
                item.status_type = item.order_status = "ALL_DONE"
                self._list_event(item)
        else:
            legs = [member for member in members if member.order_id != item.working_id]
            if order.executed > 0 or order.status in ("EXPIRED", "CANCELED", "REJECTED"):
                for other in legs:
                    if other is not order and other.status in OPEN:
                        self._finish(other, "EXPIRED")
                        self._event(other, "EXPIRED")
                if all(member.status in FINAL for member in legs) or order.executed > 0:
                    item.status_type = item.order_status = "ALL_DONE"
                    self._list_event(item)
        self._balance_event(*assets)

    def _activate(self, item: OrderList, members: list[Order]) -> None:
        pending = [member for member in members if member.status == "PENDING_NEW"]
        if not pending:
            return
        base = SYMBOLS[item.symbol]["base"]
        qty = pending[0].qty
        if self._free(base) < qty:
            for member in pending:
                member.status = "REJECTED"
                self._event(member, "REJECTED")
            item.status_type = item.order_status = "ALL_DONE"
            self._list_event(item)
            return
        lock = self._lock_for(pending[0])
        for member in pending[1:]:
            self._lock_for(member, lock)
        for member in pending:
            member.status = "NEW"
            member.update = self.clock_ms()
            self._event(member, "NEW")
        # Hedef hemen eşleşecekse ya da stop hemen tetiklenecekse (fiyat zaten oradaysa)
        # borsa emri yine koyar; bir sonraki işlemde eşleşir.

    # --- olaylar --------------------------------------------------------------------------

    def _publish(self, event: dict[str, Any]) -> None:
        text = json.dumps({"subscriptionId": 0, "event": event})
        if not self.stream_up:
            self.lost_events += 1
            return
        for listener in list(self.listeners):
            listener(text)

    def _event(self, order: Order, exec_type: str, *, trade: dict[str, Any] | None = None,
               cancel_id: str | None = None) -> None:
        now = self.clock_ms()
        self._publish({
            "e": "executionReport", "E": now, "s": order.symbol,
            "c": cancel_id or order.client_id, "S": order.side, "o": order.type,
            "f": order.tif, "q": _s(order.qty), "p": _s(order.price),
            "P": _s(order.stop_price), "F": "0.00000000", "g": order.list_id,
            "C": order.client_id if cancel_id else "", "x": exec_type, "X": order.status,
            "r": "NONE", "i": order.order_id,
            "l": trade["qty"] if trade else "0.00000000", "z": _s(order.executed),
            "L": trade["price"] if trade else "0.00000000",
            "n": trade["commission"] if trade else "0",
            "N": trade["commissionAsset"] if trade else None, "T": now,
            "t": trade["id"] if trade else -1, "w": order.status != "PENDING_NEW",
            "m": bool(trade["isMaker"]) if trade else False, "O": order.time,
            "Z": _s(order.quote), "Y": trade["quoteQty"] if trade else "0.00000000",
            "Q": "0.00000000", "W": order.time, "V": "NONE",
            **({"eR": order.expiry_reason} if order.expiry_reason else {}),
        })

    def _list_event(self, item: OrderList) -> None:
        now = self.clock_ms()
        self._publish({
            "e": "listStatus", "E": now, "s": item.symbol, "g": item.list_id,
            "c": item.contingency, "l": item.status_type, "L": item.order_status, "r": "NONE",
            "C": item.client_id, "T": now,
            "O": [{"s": item.symbol, "i": order_id, "c": self.orders[order_id].client_id}
                  for order_id in item.order_ids],
        })

    def _balance_event(self, *assets: str) -> None:
        now = self.clock_ms()
        self._publish({
            "e": "outboundAccountPosition", "E": now, "u": now,
            "B": [{"a": asset, "f": _s(self.balances[asset][0]),
                   "l": _s(self.balances[asset][1])}
                  for asset in assets if asset in self.balances],
        })

    # --- herkese açık uçlar (``PublicHttp`` yerine) --------------------------------------

    def exchange_info(self, symbols: list[str] | None = None) -> dict[str, Any]:
        return exchange_info(symbols)

    def book_tickers(self, symbols: list[str]) -> list[dict[str, Any]]:
        return [{"symbol": symbol, "bidPrice": _s(self.book[symbol][0]),
                 "askPrice": _s(self.book[symbol][1])} for symbol in symbols]


@dataclass
class FakeUserStream:
    """``UserStream`` yerine: sahte borsanın olaylarını doğrudan yürütücüye verir."""

    exchange: FakeBinance
    on_event: Callable[[Any], None]
    on_state: Callable[[bool, str], None] | None = None
    server_time_ms: Callable[[], int] | None = None
    subscribed: bool = False
    reconnects: int = 0
    events: int = 0
    parse: Callable[[str], Any] | None = None
    _listener: Callable[[str], None] | None = field(default=None, repr=False)

    def start(self) -> None:
        if self._listener is None:
            from albsat.exchange.user_stream import parse_user_message

            def listener(text: str) -> None:
                if not self.subscribed:
                    return
                event = parse_user_message(text)
                if event is not None:
                    self.events += 1
                    self.on_event(event)

            self._listener = listener
            self.exchange.listeners.append(listener)
        self.subscribed = True
        self.exchange.stream_up = True
        if self.on_state is not None:
            self.on_state(True, "abone olundu")

    def stop(self) -> None:
        self.subscribed = False

    def drop(self) -> None:
        """Bağlantı koptu: olaylar kaybolur."""
        self.subscribed = False
        self.exchange.stream_up = False
        if self.on_state is not None:
            self.on_state(False, "bağlantı koptu")

    def reconnect(self) -> None:
        self.reconnects += 1
        self.start()

    def status(self) -> Any:
        from albsat.exchange.user_stream import UserStreamStatus

        return UserStreamStatus(bagli=self.subscribed, abone=self.subscribed,
                                bagli_oldugu_an=None, son_mesaj_saniye_once=None,
                                yeniden_baglanma=self.reconnects, olay_sayisi=self.events,
                                son_hata=None if self.subscribed else "bağlantı koptu")


@dataclass
class FakeBookStream:
    exchange: FakeBinance
    on_event: Callable[[Any], None]
    connected: bool = False

    def start(self) -> None:
        from albsat.exchange.market_stream import BookEvent

        if not self.connected:
            self.connected = True
            self.exchange.book_listeners.append(
                lambda symbol, bid, ask: self.on_event(BookEvent(symbol, bid, ask)))

    def stop(self) -> None:
        self.connected = False

    def status(self) -> Any:
        from albsat.exchange.market_stream import StreamStatus

        return StreamStatus(bagli=self.connected, bagli_oldugu_an=None, son_mesaj_saniye_once=None,
                            yeniden_baglanma=0, son_hata=None, mesaj_sayisi=0)


__all__ = ["FakeBinance", "FakeBookStream", "FakeUserStream", "exchange_info"]
