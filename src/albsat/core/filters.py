"""``exchangeInfo`` sembol filtreleri: modelleme, yuvarlama ve doğrulama.

Kaynak: Binance Spot API ``filters.md`` (2026-09 tarihli sürüm).

SPEC.md §11 gereği hiçbir filtre değeri koda sabit yazılmaz; hepsi
``GET /api/v3/exchangeInfo`` yanıtından okunur ve önbelleğe alınır.

Not (2026-05-06 changelog): ``PERCENT_PRICE``, ``PERCENT_PRICE_BY_SIDE``,
``MIN_NOTIONAL`` ve ``NOTIONAL`` filtreleri, sembolün **referans fiyatı**
varsa artık onu kullanıyor. Bu yüzden doğrulama fonksiyonları ortalama fiyatı
dışarıdan parametre olarak alır — kaynağını çağıran belirler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from albsat.core.fees import Side
from albsat.core.money import (
    ONE,
    ZERO,
    Number,
    Rounding,
    is_multiple_of,
    round_to_increment,
    to_decimal,
)


@dataclass(frozen=True)
class FilterViolation:
    """Tek bir filtre ihlali; ``message`` doğrudan arayüzde gösterilebilir."""

    filter_type: str
    field: str
    message: str


@dataclass(frozen=True)
class PriceFilter:
    min_price: Decimal
    max_price: Decimal
    tick_size: Decimal


@dataclass(frozen=True)
class LotSizeFilter:
    min_qty: Decimal
    max_qty: Decimal
    step_size: Decimal


@dataclass(frozen=True)
class NotionalFilter:
    min_notional: Decimal
    max_notional: Decimal | None
    apply_min_to_market: bool
    apply_max_to_market: bool
    avg_price_mins: int


@dataclass(frozen=True)
class PercentPriceBySideFilter:
    bid_multiplier_up: Decimal
    bid_multiplier_down: Decimal
    ask_multiplier_up: Decimal
    ask_multiplier_down: Decimal
    avg_price_mins: int


@dataclass(frozen=True)
class SymbolRules:
    """Bir sembolün emir gönderirken uyulması gereken tüm kuralları."""

    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    order_types: tuple[str, ...] = ()
    oco_allowed: bool = False
    oto_allowed: bool = False
    opo_allowed: bool = False
    allow_trailing_stop: bool = False
    cancel_replace_allowed: bool = False
    amend_allowed: bool = False
    peg_instructions_allowed: bool = False
    is_spot_trading_allowed: bool = False
    price: PriceFilter | None = None
    lot: LotSizeFilter | None = None
    market_lot: LotSizeFilter | None = None
    notional: NotionalFilter | None = None
    percent_price_by_side: PercentPriceBySideFilter | None = None
    max_num_orders: int | None = None
    max_num_order_lists: int | None = None
    raw_filters: tuple[Mapping[str, object], ...] = field(default_factory=tuple)

    # --- durum ---------------------------------------------------------

    @property
    def tradable(self) -> bool:
        """Yeni pozisyon açmaya uygun mu?

        ``CANCEL_ONLY`` (2026-07-07 changelog) ve ``TRADING`` dışındaki her
        durumda yeni emir gönderilmez.
        """
        return self.status == "TRADING" and self.is_spot_trading_allowed

    @property
    def otoco_allowed(self) -> bool:
        """OTOCO için hem OTO hem OCO desteği gerekir."""
        return self.oto_allowed and self.oco_allowed

    # --- yuvarlama -----------------------------------------------------

    def round_price(self, price: Number, side: Side) -> Decimal:
        """Fiyatı ``tickSize``'a, **aleyhimize olmayacak** yönde yuvarlar.

        ``BUY`` limit emrinde aşağı (daha fazla ödememek için), ``SELL``
        limit emrinde yukarı yuvarlanır.
        """
        if self.price is None:
            return to_decimal(price)
        mode = Rounding.FLOOR if side is Side.BUY else Rounding.CEILING
        return round_to_increment(
            price, self.price.tick_size, mode, base=self.price.min_price
        )

    def round_quantity(self, quantity: Number) -> Decimal:
        """Miktarı ``stepSize``'a **her zaman aşağı** yuvarlar.

        Yukarı yuvarlamak hedeflenenden büyük pozisyon açmak demektir; risk
        motorunun hesapladığı büyüklük asla aşılmaz.
        """
        if self.lot is None:
            return to_decimal(quantity)
        return round_to_increment(
            quantity, self.lot.step_size, Rounding.FLOOR, base=self.lot.min_qty
        )

    def min_quantity_for_notional(self, price: Number) -> Decimal:
        """Verilen fiyatta ``minNotional``'ı sağlayan en küçük geçerli miktar."""
        price = to_decimal(price)
        if self.notional is None or price <= ZERO:
            return self.lot.min_qty if self.lot else ZERO
        needed = self.notional.min_notional / price
        step = self.lot.step_size if self.lot else ZERO
        base = self.lot.min_qty if self.lot else ZERO
        candidate = round_to_increment(needed, step, Rounding.CEILING, base=base)
        if self.lot and candidate < self.lot.min_qty:
            candidate = self.lot.min_qty
        return candidate

    # --- doğrulama -----------------------------------------------------

    def validate_limit_order(
        self,
        *,
        side: Side,
        price: Number,
        quantity: Number,
        reference_price: Number | None = None,
    ) -> list[FilterViolation]:
        """Limit emrini filtrelere karşı doğrular; boş liste = geçerli."""
        price = to_decimal(price)
        quantity = to_decimal(quantity)
        violations: list[FilterViolation] = []

        if not self.tradable:
            violations.append(
                FilterViolation(
                    "SYMBOL_STATUS",
                    "status",
                    f"{self.symbol} şu anda işleme kapalı (durum: {self.status}).",
                )
            )

        if self.price is not None:
            pf = self.price
            if pf.min_price > ZERO and price < pf.min_price:
                violations.append(
                    FilterViolation(
                        "PRICE_FILTER",
                        "minPrice",
                        f"Fiyat {price}, izin verilen en düşük fiyatın ({pf.min_price}) altında.",
                    )
                )
            if pf.max_price > ZERO and price > pf.max_price:
                violations.append(
                    FilterViolation(
                        "PRICE_FILTER",
                        "maxPrice",
                        f"Fiyat {price}, izin verilen en yüksek fiyatın ({pf.max_price}) üstünde.",
                    )
                )
            if not is_multiple_of(price, pf.tick_size, base=pf.min_price):
                violations.append(
                    FilterViolation(
                        "PRICE_FILTER",
                        "tickSize",
                        f"Fiyat {price}, {pf.tick_size} adımının katı değil.",
                    )
                )

        if self.lot is not None:
            lf = self.lot
            if quantity < lf.min_qty:
                violations.append(
                    FilterViolation(
                        "LOT_SIZE",
                        "minQty",
                        f"Miktar {quantity}, en düşük miktarın ({lf.min_qty}) altında.",
                    )
                )
            if lf.max_qty > ZERO and quantity > lf.max_qty:
                violations.append(
                    FilterViolation(
                        "LOT_SIZE",
                        "maxQty",
                        f"Miktar {quantity}, en yüksek miktarın ({lf.max_qty}) üstünde.",
                    )
                )
            if not is_multiple_of(quantity, lf.step_size, base=lf.min_qty):
                violations.append(
                    FilterViolation(
                        "LOT_SIZE",
                        "stepSize",
                        f"Miktar {quantity}, {lf.step_size} adımının katı değil.",
                    )
                )

        if self.notional is not None:
            notional = price * quantity
            nf = self.notional
            if notional < nf.min_notional:
                violations.append(
                    FilterViolation(
                        "NOTIONAL",
                        "minNotional",
                        f"Emir tutarı {notional} USDT, en düşük tutarın "
                        f"({nf.min_notional} USDT) altında.",
                    )
                )
            if nf.max_notional is not None and nf.max_notional > ZERO and notional > nf.max_notional:
                violations.append(
                    FilterViolation(
                        "NOTIONAL",
                        "maxNotional",
                        f"Emir tutarı {notional} USDT, en yüksek tutarın "
                        f"({nf.max_notional} USDT) üstünde.",
                    )
                )

        if self.percent_price_by_side is not None and reference_price is not None:
            ref = to_decimal(reference_price)
            pp = self.percent_price_by_side
            if side is Side.BUY:
                upper, lower = ref * pp.bid_multiplier_up, ref * pp.bid_multiplier_down
            else:
                upper, lower = ref * pp.ask_multiplier_up, ref * pp.ask_multiplier_down
            if price > upper or price < lower:
                violations.append(
                    FilterViolation(
                        "PERCENT_PRICE_BY_SIDE",
                        "multiplier",
                        f"Fiyat {price}, referans fiyata göre izin verilen "
                        f"[{lower}, {upper}] aralığının dışında.",
                    )
                )

        return violations

    # --- kurulum -------------------------------------------------------

    @classmethod
    def from_exchange_info(cls, symbol_payload: Mapping[str, object]) -> "SymbolRules":
        """``exchangeInfo`` içindeki tek bir sembol nesnesinden kurar."""
        raw_filters: Sequence[Mapping[str, object]] = tuple(
            symbol_payload.get("filters", ())  # type: ignore[arg-type]
        )
        by_type = {str(f.get("filterType")): f for f in raw_filters}

        def dec(source: Mapping[str, object], key: str, default: str = "0") -> Decimal:
            return to_decimal(str(source.get(key, default)))

        price_filter = None
        if "PRICE_FILTER" in by_type:
            f = by_type["PRICE_FILTER"]
            price_filter = PriceFilter(dec(f, "minPrice"), dec(f, "maxPrice"), dec(f, "tickSize"))

        def lot_from(name: str) -> LotSizeFilter | None:
            if name not in by_type:
                return None
            f = by_type[name]
            return LotSizeFilter(dec(f, "minQty"), dec(f, "maxQty"), dec(f, "stepSize"))

        notional = None
        # NOTIONAL yeni, MIN_NOTIONAL eski adı; ikisi de görülebilir.
        for name in ("NOTIONAL", "MIN_NOTIONAL"):
            if name in by_type:
                f = by_type[name]
                max_notional = dec(f, "maxNotional") if "maxNotional" in f else None
                notional = NotionalFilter(
                    min_notional=dec(f, "minNotional"),
                    max_notional=max_notional,
                    apply_min_to_market=bool(f.get("applyMinToMarket", False)),
                    apply_max_to_market=bool(f.get("applyMaxToMarket", False)),
                    avg_price_mins=int(f.get("avgPriceMins", 0) or 0),  # type: ignore[arg-type]
                )
                break

        percent = None
        if "PERCENT_PRICE_BY_SIDE" in by_type:
            f = by_type["PERCENT_PRICE_BY_SIDE"]
            percent = PercentPriceBySideFilter(
                bid_multiplier_up=dec(f, "bidMultiplierUp", "0"),
                bid_multiplier_down=dec(f, "bidMultiplierDown", "0"),
                ask_multiplier_up=dec(f, "askMultiplierUp", "0"),
                ask_multiplier_down=dec(f, "askMultiplierDown", "0"),
                avg_price_mins=int(f.get("avgPriceMins", 0) or 0),  # type: ignore[arg-type]
            )

        def num(name: str, key: str = "maxNumOrders") -> int | None:
            f = by_type.get(name)
            return int(f[key]) if f and key in f else None  # type: ignore[arg-type]

        return cls(
            symbol=str(symbol_payload["symbol"]),
            base_asset=str(symbol_payload.get("baseAsset", "")),
            quote_asset=str(symbol_payload.get("quoteAsset", "")),
            status=str(symbol_payload.get("status", "")),
            order_types=tuple(str(t) for t in symbol_payload.get("orderTypes", ())),  # type: ignore[arg-type]
            oco_allowed=bool(symbol_payload.get("ocoAllowed", False)),
            oto_allowed=bool(symbol_payload.get("otoAllowed", False)),
            opo_allowed=bool(symbol_payload.get("opoAllowed", False)),
            allow_trailing_stop=bool(symbol_payload.get("allowTrailingStop", False)),
            cancel_replace_allowed=bool(symbol_payload.get("cancelReplaceAllowed", False)),
            amend_allowed=bool(symbol_payload.get("amendAllowed", False)),
            peg_instructions_allowed=bool(symbol_payload.get("pegInstructionsAllowed", False)),
            is_spot_trading_allowed=bool(symbol_payload.get("isSpotTradingAllowed", False)),
            price=price_filter,
            lot=lot_from("LOT_SIZE"),
            market_lot=lot_from("MARKET_LOT_SIZE"),
            notional=notional,
            percent_price_by_side=percent,
            max_num_orders=num("MAX_NUM_ORDERS"),
            max_num_order_lists=num("MAX_NUM_ORDER_LISTS", "maxNumOrderLists"),
            raw_filters=tuple(raw_filters),
        )


def parse_exchange_info(payload: Mapping[str, object]) -> dict[str, SymbolRules]:
    """``exchangeInfo`` yanıtının tamamını sembol → kural sözlüğüne çevirir."""
    symbols: Iterable[Mapping[str, object]] = payload.get("symbols", ())  # type: ignore[assignment]
    return {
        str(s["symbol"]): SymbolRules.from_exchange_info(s) for s in symbols
    }
