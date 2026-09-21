"""Başa-baş ve maliyet testleri."""
from decimal import Decimal

import pytest

from albsat.core.costs import FeePayment, minimum_meaningful_target, round_trip_for
from albsat.core.fees import Liquidity, flat_table


def rt(entry=Liquidity.MAKER, exit_=Liquidity.MAKER, bnb=False):
    return round_trip_for(
        flat_table("BTCUSDT", "0.001", "0.001"),
        entry_liquidity=entry,
        exit_liquidity=exit_,
        has_discount_asset_balance=bnb,
    )


def test_basabas_fiyatinda_net_kar_sifirdir():
    trip = rt()
    entry = Decimal("100")
    break_even = trip.break_even_price(entry)
    pnl = trip.net_pnl_quote(entry, break_even, Decimal("1"))
    assert abs(pnl) < Decimal("0.0000000001")


def test_basabas_giris_fiyatinin_ustundedir():
    trip = rt()
    assert trip.break_even_price("100") > Decimal("100")


def test_stop_bacagi_maker_cikistan_pahalidir():
    # STOP_LOSS tetiklenince piyasa emridir; taker komisyonu uygulanır.
    ucuz = rt(exit_=Liquidity.MAKER)
    pahali = round_trip_for(
        flat_table("BTCUSDT", "0.001", "0.002"),
        entry_liquidity=Liquidity.MAKER,
        exit_liquidity=Liquidity.TAKER,
    )
    assert pahali.total_fee_rate > ucuz.total_fee_rate


def test_basabasin_altinda_satis_zarardir():
    trip = rt()
    break_even = trip.break_even_price("100")
    assert trip.net_pnl_quote("100", break_even - Decimal("0.01"), "1") < 0


def test_minimum_hedef_tum_bilesenleri_toplar():
    threshold = minimum_meaningful_target(
        rt(), spread_pct="0.01", slippage_pct="0.02", safety_pct="0.05"
    )
    assert threshold.minimum_target_pct == Decimal("0.2") + Decimal("0.08")
    assert not threshold.passes("0.2")
    assert threshold.passes("0.28")


def test_bnb_odemesi_farkli_formul_kullanir():
    trip = rt(bnb=False)
    assert trip.payment is FeePayment.FROM_RECEIVED
    table = flat_table("BTCUSDT", "0.001", "0.001")
    # flat_table'da indirim kapalı olduğu için BNB yolu tetiklenmez;
    # formülü doğrudan doğrula.
    from albsat.core.costs import LegCost, RoundTrip
    from albsat.core.fees import Side
    bnb_trip = RoundTrip(
        entry=LegCost(Side.BUY, Liquidity.MAKER, Decimal("0.001")),
        exit=LegCost(Side.SELL, Liquidity.MAKER, Decimal("0.001")),
        payment=FeePayment.IN_BNB,
    )
    pnl = bnb_trip.net_pnl_quote("100", bnb_trip.break_even_price("100"), "1")
    assert abs(pnl) < Decimal("0.0000000001")
