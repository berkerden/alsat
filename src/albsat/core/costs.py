"""Gidiş-dönüş maliyet, başa-baş fiyatı ve "anlamlı hedef" eşiği.

SPEC.md §4.3'teki kritik kural burada uygulanır:

    Minimum anlamlı hedef = alış komisyonu + satış komisyonu + spread
                            + beklenen kayma + güvenlik payı

Ve §4.4'teki "her öneride başa-baş fiyatını göster" gereği.

Kolay gözden kaçan nokta (FAZ0-MIMARI.md Risk #4): tek bir "gidiş-dönüş
komisyonu" sabiti yanlıştır. Giriş ``LIMIT_MAKER`` ise maker, ama stop
``STOP_LOSS`` tetiklendiğinde **piyasa emri**, yani taker'dır. Bu yüzden
hedefe giden işlem ile stopa giden işlemin maliyeti farklıdır ve ikisi de
ayrı ayrı hesaplanır.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from albsat.core.fees import CommissionTable, Liquidity, Side
from albsat.core.money import ONE, ONE_HUNDRED, ZERO, Number, to_decimal


class FeePayment(str, Enum):
    """Komisyonun nereden kesildiği.

    ``FROM_RECEIVED``: komisyon alınan varlıktan düşülür (BNB yok/bitmiş).
    ``IN_BNB``: komisyon ayrı bir varlıktan (BNB) ödenir, alınan miktar tam kalır.
    """

    FROM_RECEIVED = "FROM_RECEIVED"
    IN_BNB = "IN_BNB"


@dataclass(frozen=True)
class LegCost:
    """Tek bir bacağın (giriş veya çıkış) maliyet bilgisi."""

    side: Side
    liquidity: Liquidity
    rate: Decimal

    @property
    def rate_pct(self) -> Decimal:
        return self.rate * ONE_HUNDRED


@dataclass(frozen=True)
class RoundTrip:
    """Bir al-sat turunun maliyet özeti."""

    entry: LegCost
    exit: LegCost
    payment: FeePayment

    @property
    def total_fee_rate(self) -> Decimal:
        """İki bacağın komisyon oranları toplamı (yaklaşık gidiş-dönüş maliyeti)."""
        return self.entry.rate + self.exit.rate

    @property
    def total_fee_pct(self) -> Decimal:
        return self.total_fee_rate * ONE_HUNDRED

    def break_even_price(self, entry_price: Number) -> Decimal:
        """Net kâr/zararın tam sıfır olduğu çıkış fiyatı.

        ``FROM_RECEIVED``: alışta coinin bir kısmı komisyona gider, satışta
        gelen USDT'nin bir kısmı gider::

            P_out = P_in / ((1 - f_giris) * (1 - f_cikis))

        ``IN_BNB``: miktar tam kalır, komisyon her iki bacakta da notional
        üzerinden ayrıca ödenir::

            P_out = P_in * (1 + f_giris) / (1 - f_cikis)
        """
        entry_price = to_decimal(entry_price)
        f_in, f_out = self.entry.rate, self.exit.rate
        if self.payment is FeePayment.FROM_RECEIVED:
            denominator = (ONE - f_in) * (ONE - f_out)
            if denominator <= ZERO:
                raise ValueError("Komisyon oranları %100'ü aşıyor; başa-baş hesaplanamaz")
            return entry_price / denominator
        denominator = ONE - f_out
        if denominator <= ZERO:
            raise ValueError("Çıkış komisyonu %100'ü aşıyor; başa-baş hesaplanamaz")
        return entry_price * (ONE + f_in) / denominator

    def net_pnl_quote(
        self, entry_price: Number, exit_price: Number, quantity: Number
    ) -> Decimal:
        """Kotasyon varlığı (USDT) cinsinden net kâr/zarar."""
        entry_price = to_decimal(entry_price)
        exit_price = to_decimal(exit_price)
        quantity = to_decimal(quantity)
        f_in, f_out = self.entry.rate, self.exit.rate

        if self.payment is FeePayment.FROM_RECEIVED:
            received_base = quantity * (ONE - f_in)
            proceeds = exit_price * received_base * (ONE - f_out)
            return proceeds - entry_price * quantity

        gross = (exit_price - entry_price) * quantity
        fees = f_in * entry_price * quantity + f_out * exit_price * quantity
        return gross - fees

    def net_margin_pct(self, entry_price: Number, exit_price: Number) -> Decimal:
        """Girilen sermayeye göre net marj yüzdesi (miktardan bağımsız)."""
        entry_price = to_decimal(entry_price)
        pnl = self.net_pnl_quote(entry_price, exit_price, ONE)
        if entry_price == ZERO:
            return ZERO
        return pnl / entry_price * ONE_HUNDRED


def round_trip_for(
    commissions: CommissionTable,
    *,
    entry_liquidity: Liquidity,
    exit_liquidity: Liquidity,
    has_discount_asset_balance: bool = False,
) -> RoundTrip:
    """Spot al-sat turu: giriş ``BUY``, çıkış ``SELL``."""
    payment = (
        FeePayment.IN_BNB
        if commissions.discount.applies(has_discount_asset_balance)
        else FeePayment.FROM_RECEIVED
    )
    return RoundTrip(
        entry=LegCost(
            side=Side.BUY,
            liquidity=entry_liquidity,
            rate=commissions.effective_rate(
                Side.BUY,
                entry_liquidity,
                has_discount_asset_balance=has_discount_asset_balance,
            ),
        ),
        exit=LegCost(
            side=Side.SELL,
            liquidity=exit_liquidity,
            rate=commissions.effective_rate(
                Side.SELL,
                exit_liquidity,
                has_discount_asset_balance=has_discount_asset_balance,
            ),
        ),
        payment=payment,
    )


@dataclass(frozen=True)
class TargetThreshold:
    """SPEC.md §4.3'teki "minimum anlamlı hedef" eşiğinin bileşenleri.

    Tüm alanlar **yüzde** cinsindendir, hepsi giriş fiyatına oranla.
    """

    fee_pct: Decimal
    spread_pct: Decimal
    slippage_pct: Decimal
    safety_pct: Decimal

    @property
    def minimum_target_pct(self) -> Decimal:
        return self.fee_pct + self.spread_pct + self.slippage_pct + self.safety_pct

    def passes(self, target_pct: Number) -> bool:
        """Önerilen hedef bu eşiği geçiyor mu? Geçmiyorsa sinyal elenir."""
        return to_decimal(target_pct) >= self.minimum_target_pct

    def explain(self) -> str:
        """Arayüzde ve öneri kartında gösterilecek Türkçe açıklama."""
        return (
            f"Komisyon %{self.fee_pct:.4f} + spread %{self.spread_pct:.4f} + "
            f"kayma %{self.slippage_pct:.4f} + güvenlik payı %{self.safety_pct:.4f} "
            f"= en az %{self.minimum_target_pct:.4f}"
        )


def minimum_meaningful_target(
    round_trip: RoundTrip,
    *,
    spread_pct: Number,
    slippage_pct: Number,
    safety_pct: Number,
) -> TargetThreshold:
    """Bir sinyalin elenmeden geçebilmesi için gereken en küçük brüt hedef."""
    return TargetThreshold(
        fee_pct=round_trip.total_fee_pct,
        spread_pct=to_decimal(spread_pct),
        slippage_pct=to_decimal(slippage_pct),
        safety_pct=to_decimal(safety_pct),
    )
