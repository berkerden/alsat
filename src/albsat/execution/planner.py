"""Borsaya gidecek emirlerin hazırlanması: OTOCO, yeniden koruma (OCO), çıkış.

Planlayıcı ağa çıkmaz. Fiyatı ``tickSize``'a, miktarı ``stepSize``'a
yuvarlar, her bacağı borsa filtrelerine karşı sınar ve gönderilecek
parametreleri üretir. Bir bacak filtreye uymuyorsa emir hiç gönderilmez;
kullanıcı sebebi Türkçe görür.

**Satış miktarı (``pendingQuantity``).** Alışta komisyon alınan coinden
kesilir: 0,00100 BTC alan hesaba ~0,000999 BTC girer ve satış emri
``stepSize``'ın katı olmak zorundadır. OTOCO'nun hedef ve stop emirleri
giriş dolduğu anda borsaya konur; miktarları giriş anında bilinmelidir.
Bu yüzden satış miktarı ``(alış × (1 − komisyon)) + önceki toz``'un aşağı
yuvarlanmışıdır. Komisyon oranı olarak maker ve taker'ın büyüğü alınır
(temkinli: satış miktarı eldekinden fazla olursa borsa hedef ve stopu
"yetersiz bakiye" ile reddeder, pozisyon korumasız kalır). Artan küsurat
bir sonraki işlemin satışına eklenir.

Binance'in OPO türü (bekleyen miktarı alınan miktardan kendisi hesaplar)
belgelenmiş bir seçenek olarak not edildi; bu fazda OTOCO + hesaplanmış
miktar kullanılıyor, çünkü Faz 0 mimarisi ve kâğıt işlem bu modeli izliyor.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from albsat.core.fees import Side
from albsat.core.filters import FilterViolation, SymbolRules
from albsat.core.money import ONE_HUNDRED, ZERO, Rounding, format_for_api, round_to_increment
from albsat.execution import ids
from albsat.execution.ledger import (
    LEG_SENDING,
    ROLE_ENTRY,
    ROLE_EXIT,
    ROLE_STOP,
    ROLE_TARGET,
)
from albsat.execution.settings import STOP_LIMIT, STOP_MARKET, ExecutionSettings


@dataclass(frozen=True)
class Plan:
    params: dict[str, str]
    legs: list[dict[str, object]]
    sorunlar: tuple[str, ...]
    satis_miktari: Decimal = ZERO
    stop_limit: Decimal | None = None

    @property
    def gecerli(self) -> bool:
        return not self.sorunlar


def _m(value: Decimal) -> str:
    return format_for_api(value)


def floor_price(rules: SymbolRules, value: Decimal) -> Decimal:
    if rules.price is None:
        return value
    return round_to_increment(value, rules.price.tick_size, Rounding.FLOOR,
                              base=rules.price.min_price)


def sell_quantity(rules: SymbolRules, *, bought: Decimal, fee_rate: Decimal,
                  carried_dust: Decimal) -> Decimal:
    """Alıştan sonra satılabilecek miktar (aşağı yuvarlanmış)."""
    available = bought * (Decimal(1) - fee_rate) + max(carried_dust, ZERO)
    quantity = rules.round_quantity(available)
    return max(quantity, ZERO)


def _texts(violations: list[FilterViolation], label: str) -> list[str]:
    return [f"{label}: {item.message}" for item in violations]


def _stop_checks(rules: SymbolRules, *, stop: Decimal, quantity: Decimal,
                 settings: ExecutionSettings, label: str) -> tuple[list[str], Decimal | None]:
    """Stop bacağı: fiyat adımı, miktar ve tutar. Limitli stopta limit fiyatı da."""
    problems: list[str] = []
    limit: Decimal | None = None
    wanted = settings.stop_tipi
    if rules.order_types and wanted not in rules.order_types:
        problems.append(f"{label}: {rules.symbol} {wanted} emir tipini kabul etmiyor.")
    if settings.stop_tipi == STOP_LIMIT:
        limit = floor_price(rules, stop * (ONE_HUNDRED - settings.stop_limit_ofset_yuzde)
                            / ONE_HUNDRED)
        problems.extend(_texts(rules.validate_limit_order(side=Side.SELL, price=limit,
                                                          quantity=quantity), label))
    # Stop fiyatı da tickSize'ın katı olmalı; miktar ve tutar stop fiyatından sınanır
    # (piyasa stopunda dolum fiyatı bilinmez, stop en temkinli tahmindir).
    problems.extend(_texts(rules.validate_limit_order(side=Side.SELL, price=stop,
                                                      quantity=quantity), label))
    return list(dict.fromkeys(problems)), limit


def plan_otoco(
    *,
    rules: SymbolRules,
    token: str,
    attempt: int,
    entry: Decimal,
    target: Decimal,
    stop: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    carried_dust: Decimal,
    settings: ExecutionSettings,
) -> Plan:
    problems: list[str] = []
    if not rules.otoco_allowed:
        problems.append(f"{rules.symbol} OTOCO emir listesini desteklemiyor.")
    if rules.order_types and "LIMIT_MAKER" not in rules.order_types:
        problems.append(f"{rules.symbol} LIMIT_MAKER emir tipini kabul etmiyor.")
    if not target > entry > stop > ZERO:
        problems.append("Fiyat sırası hatalı: hedef > giriş > stop > 0 olmalı.")
    entry = rules.round_price(entry, Side.BUY)
    target = rules.round_price(target, Side.SELL)
    stop = floor_price(rules, stop)
    quantity = rules.round_quantity(quantity)
    pending = sell_quantity(rules, bought=quantity, fee_rate=fee_rate, carried_dust=carried_dust)
    problems.extend(_texts(rules.validate_limit_order(side=Side.BUY, price=entry,
                                                      quantity=quantity), "Giriş"))
    problems.extend(_texts(rules.validate_limit_order(side=Side.SELL, price=target,
                                                      quantity=pending), "Hedef"))
    stop_problems, limit = _stop_checks(rules, stop=stop, quantity=pending, settings=settings,
                                        label="Stop")
    problems.extend(stop_problems)
    names = ids.otoco_ids(token, attempt)
    params: dict[str, str] = {
        "symbol": rules.symbol,
        "listClientOrderId": names["listClientOrderId"],
        "workingType": "LIMIT_MAKER",
        "workingSide": "BUY",
        "workingClientOrderId": names["workingClientOrderId"],
        "workingPrice": _m(entry),
        "workingQuantity": _m(quantity),
        "pendingSide": "SELL",
        "pendingQuantity": _m(pending),
        "pendingAboveType": "LIMIT_MAKER",
        "pendingAboveClientOrderId": names["pendingAboveClientOrderId"],
        "pendingAbovePrice": _m(target),
        "pendingBelowType": settings.stop_tipi,
        "pendingBelowClientOrderId": names["pendingBelowClientOrderId"],
        "pendingBelowStopPrice": _m(stop),
    }
    if limit is not None:
        params["pendingBelowPrice"] = _m(limit)
        params["pendingBelowTimeInForce"] = "GTC"
    list_id = names["listClientOrderId"]
    legs: list[dict[str, object]] = [
        _leg(names["workingClientOrderId"], list_id, ROLE_ENTRY, "LIMIT_MAKER", "BUY",
             entry, None, quantity),
        _leg(names["pendingAboveClientOrderId"], list_id, ROLE_TARGET, "LIMIT_MAKER", "SELL",
             target, None, pending),
        _leg(names["pendingBelowClientOrderId"], list_id, ROLE_STOP, settings.stop_tipi, "SELL",
             limit, stop, pending),
    ]
    return Plan(params, legs, tuple(dict.fromkeys(problems)), pending, limit)


def plan_oco(
    *,
    rules: SymbolRules,
    token: str,
    attempt: int,
    target: Decimal,
    stop: Decimal,
    quantity: Decimal,
    settings: ExecutionSettings,
) -> Plan:
    """Elde tutulan miktar için hedef + stop (yeniden koruma)."""
    problems: list[str] = []
    if not rules.oco_allowed:
        problems.append(f"{rules.symbol} OCO emir listesini desteklemiyor.")
    quantity = rules.round_quantity(quantity)
    target = rules.round_price(target, Side.SELL)
    stop = floor_price(rules, stop)
    problems.extend(_texts(rules.validate_limit_order(side=Side.SELL, price=target,
                                                      quantity=quantity), "Hedef"))
    stop_problems, limit = _stop_checks(rules, stop=stop, quantity=quantity, settings=settings,
                                        label="Stop")
    problems.extend(stop_problems)
    names = ids.oco_ids(token, attempt)
    params: dict[str, str] = {
        "symbol": rules.symbol,
        "listClientOrderId": names["listClientOrderId"],
        "side": "SELL",
        "quantity": _m(quantity),
        "aboveType": "LIMIT_MAKER",
        "aboveClientOrderId": names["aboveClientOrderId"],
        "abovePrice": _m(target),
        "belowType": settings.stop_tipi,
        "belowClientOrderId": names["belowClientOrderId"],
        "belowStopPrice": _m(stop),
    }
    if limit is not None:
        params["belowPrice"] = _m(limit)
        params["belowTimeInForce"] = "GTC"
    list_id = names["listClientOrderId"]
    legs: list[dict[str, object]] = [
        _leg(names["aboveClientOrderId"], list_id, ROLE_TARGET, "LIMIT_MAKER", "SELL",
             target, None, quantity),
        _leg(names["belowClientOrderId"], list_id, ROLE_STOP, settings.stop_tipi, "SELL",
             limit, stop, quantity),
    ]
    return Plan(params, legs, tuple(dict.fromkeys(problems)), quantity, limit)


def plan_exit(
    *,
    rules: SymbolRules,
    token: str,
    attempt: int,
    quantity: Decimal,
    best_bid: Decimal,
    settings: ExecutionSettings,
) -> Plan:
    """Korumalı çıkış: en iyi alışın azami kayma kadar altında ``LIMIT IOC`` satış."""
    quantity = rules.round_quantity(quantity)
    price = floor_price(rules, best_bid * (ONE_HUNDRED - settings.azami_kayma_yuzde)
                        / ONE_HUNDRED)
    problems = _texts(rules.validate_limit_order(side=Side.SELL, price=price,
                                                 quantity=quantity), "Çıkış")
    client = ids.exit_id(token, attempt)
    params = {
        "symbol": rules.symbol,
        "side": "SELL",
        "type": "LIMIT",
        "timeInForce": "IOC",
        "quantity": _m(quantity),
        "price": _m(price),
        "newClientOrderId": client,
    }
    legs: list[dict[str, object]] = [
        _leg(client, None, ROLE_EXIT, "LIMIT", "SELL", price, None, quantity),
    ]
    return Plan(params, legs, tuple(dict.fromkeys(problems)), quantity)


def _leg(client: str, list_id: str | None, role: str, kind: str, side: str,
         price: Decimal | None, stop: Decimal | None, quantity: Decimal) -> dict[str, object]:
    return {
        "istemci_kimligi": client,
        "liste_istemci_kimligi": list_id,
        "rol": role,
        "tur": kind,
        "taraf": side,
        "fiyat": None if price is None else _m(price),
        "stop_fiyati": None if stop is None else _m(stop),
        "miktar": _m(quantity),
        "durum": LEG_SENDING,
    }


__all__ = [
    "STOP_LIMIT",
    "STOP_MARKET",
    "Plan",
    "floor_price",
    "plan_exit",
    "plan_oco",
    "plan_otoco",
    "sell_quantity",
]
