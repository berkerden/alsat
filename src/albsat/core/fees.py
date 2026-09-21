"""Komisyon modeli.

Kaynak: Binance Spot API ``GET /api/v3/account/commission`` ve
``faqs/commission_faq.md`` (2026-09 tarihli sürüm).

Dokümandan doğrulanan, kolay gözden kaçan üç kural:

1. Bir işlemin komisyon oranı **iki oranın toplamıdır**: likidite tarafı
   (``maker``/``taker``) **artı** emir yönü (``buyer``/``seller``).
2. Komisyon matrahı yöne göre değişir: ``SELL`` emrinde alınan tutar
   *notional* (USDT), ``BUY`` emrinde alınan tutar *miktar*dır (coin).
3. Üç ayrı komisyon vardır — ``standardCommission``, ``taxCommission``,
   ``specialCommission`` — ve **BNB indirimi yalnızca standart komisyona**
   uygulanır. İndirim ayrıca hem ``enabledForAccount`` hem
   ``enabledForSymbol`` açıkken ve hesapta yeterli BNB varken geçerlidir;
   BNB bitince komisyon sessizce tam orandan kesilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Mapping

from albsat.core.money import ONE_HUNDRED, ZERO, Number, to_decimal


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Liquidity(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"


@dataclass(frozen=True)
class RateSet:
    """``maker``/``taker``/``buyer``/``seller`` dörtlüsü."""

    maker: Decimal = ZERO
    taker: Decimal = ZERO
    buyer: Decimal = ZERO
    seller: Decimal = ZERO

    @classmethod
    def from_api(cls, payload: Mapping[str, str] | None) -> "RateSet":
        payload = payload or {}
        return cls(
            maker=to_decimal(payload.get("maker", "0")),
            taker=to_decimal(payload.get("taker", "0")),
            buyer=to_decimal(payload.get("buyer", "0")),
            seller=to_decimal(payload.get("seller", "0")),
        )

    def rate(self, side: Side, liquidity: Liquidity) -> Decimal:
        """Likidite tarafı + emir yönü oranlarının toplamı."""
        liq = self.maker if liquidity is Liquidity.MAKER else self.taker
        dir_ = self.buyer if side is Side.BUY else self.seller
        return liq + dir_


@dataclass(frozen=True)
class Discount:
    enabled_for_account: bool = False
    enabled_for_symbol: bool = False
    discount_asset: str = "BNB"
    # DİKKAT: Bu alan bir **çarpan**dır, indirim oranı değil.
    # commission_faq.md'deki iki örnekte de ödenen tutar
    # `standart_komisyon * discount` olarak hesaplanıyor. rest-api.md'deki
    # düzyazı açıklama ("reduced by this rate") bununla çelişiyor.
    # Faz 5'te `POST /api/v3/order/test` + `computeCommissionRates` ile
    # canlı olarak doğrulanacak; o zamana kadar FAQ'teki formül esastır.
    multiplier: Decimal = Decimal("1")

    @classmethod
    def from_api(cls, payload: Mapping[str, object] | None) -> "Discount":
        payload = payload or {}
        raw = payload.get("discount", "1")
        return cls(
            enabled_for_account=bool(payload.get("enabledForAccount", False)),
            enabled_for_symbol=bool(payload.get("enabledForSymbol", False)),
            discount_asset=str(payload.get("discountAsset", "BNB")),
            multiplier=to_decimal(raw if isinstance(raw, (str, int, Decimal)) else "1"),
        )

    def applies(self, has_discount_asset_balance: bool) -> bool:
        return (
            self.enabled_for_account
            and self.enabled_for_symbol
            and has_discount_asset_balance
        )


@dataclass(frozen=True)
class CommissionTable:
    """Bir sembol için hesabın güncel komisyon yapısı."""

    symbol: str
    standard: RateSet
    tax: RateSet
    special: RateSet
    discount: Discount

    @classmethod
    def from_api(cls, payload: Mapping[str, object]) -> "CommissionTable":
        """``GET /api/v3/account/commission`` yanıtından kurar."""
        return cls(
            symbol=str(payload["symbol"]),
            standard=RateSet.from_api(payload.get("standardCommission")),  # type: ignore[arg-type]
            tax=RateSet.from_api(payload.get("taxCommission")),  # type: ignore[arg-type]
            special=RateSet.from_api(payload.get("specialCommission")),  # type: ignore[arg-type]
            discount=Discount.from_api(payload.get("discount")),  # type: ignore[arg-type]
        )

    def effective_rate(
        self,
        side: Side,
        liquidity: Liquidity,
        *,
        has_discount_asset_balance: bool = False,
    ) -> Decimal:
        """Bu bacak için fiilen ödenecek toplam komisyon oranı.

        İndirim yalnızca standart komisyona uygulanır; vergi ve özel
        komisyon tam orandan kalır.
        """
        standard = self.standard.rate(side, liquidity)
        if self.discount.applies(has_discount_asset_balance):
            standard = standard * self.discount.multiplier
        return standard + self.tax.rate(side, liquidity) + self.special.rate(side, liquidity)

    def effective_rate_pct(
        self,
        side: Side,
        liquidity: Liquidity,
        *,
        has_discount_asset_balance: bool = False,
    ) -> Decimal:
        return (
            self.effective_rate(
                side, liquidity, has_discount_asset_balance=has_discount_asset_balance
            )
            * ONE_HUNDRED
        )


def flat_table(
    symbol: str,
    maker: Number,
    taker: Number,
) -> CommissionTable:
    """Testler ve "borsaya bağlanmadan önce" senaryoları için düz oranlı tablo.

    Üretimde **kullanılmaz**: gerçek oranlar her zaman borsadan çekilir
    (SPEC.md §11 "Komisyon oranlarını koda sabit yazma").
    """
    return CommissionTable(
        symbol=symbol,
        standard=RateSet(maker=to_decimal(maker), taker=to_decimal(taker)),
        tax=RateSet(),
        special=RateSet(),
        discount=Discount(),
    )
