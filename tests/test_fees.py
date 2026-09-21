"""Komisyon testleri: sayılar Binance'in commission_faq.md örneğinden alındı."""
from decimal import Decimal

from albsat.core.fees import CommissionTable, Liquidity, Side

# faqs/commission_faq.md "How is the commission calculated?" bölümündeki tablo
FAQ_PAYLOAD = {
    "symbol": "BTCUSDT",
    "standardCommission": {
        "maker": "0.00000010", "taker": "0.00000020",
        "buyer": "0.00000030", "seller": "0.00000040",
    },
    "specialCommission": {
        "maker": "0.01000000", "taker": "0.02000000",
        "buyer": "0.03000000", "seller": "0.04000000",
    },
    "taxCommission": {
        "maker": "0.00000112", "taker": "0.00000114",
        "buyer": "0.00000118", "seller": "0.00000116",
    },
    "discount": {
        "enabledForAccount": True, "enabledForSymbol": True,
        "discountAsset": "BNB", "discount": "0.25000000",
    },
}


def table():
    return CommissionTable.from_api(FAQ_PAYLOAD)


def test_oran_likidite_ve_yon_toplamidir():
    # FAQ: Standard = taker + seller = 0.00000020 + 0.00000040
    assert table().standard.rate(Side.SELL, Liquidity.TAKER) == Decimal("0.00000060")
    # FAQ: Tax = 0.00000114 + 0.00000116
    assert table().tax.rate(Side.SELL, Liquidity.TAKER) == Decimal("0.00000230")
    # FAQ: Special = 0.02000000 + 0.04000000
    assert table().special.rate(Side.SELL, Liquidity.TAKER) == Decimal("0.06000000")


def test_buy_tarafi_buyer_oranini_kullanir():
    assert table().standard.rate(Side.BUY, Liquidity.MAKER) == Decimal("0.00000040")


def test_bnb_indirimi_yalnizca_standarda_uygulanir():
    t = table()
    without = t.effective_rate(Side.SELL, Liquidity.TAKER)
    with_bnb = t.effective_rate(Side.SELL, Liquidity.TAKER, has_discount_asset_balance=True)

    standard = Decimal("0.00000060")
    tax_and_special = Decimal("0.00000230") + Decimal("0.06000000")

    assert without == standard + tax_and_special
    # İndirim sadece standart bileşeni çarpar; vergi ve özel aynen kalır.
    assert with_bnb == standard * Decimal("0.25") + tax_and_special


def test_bnb_bakiyesi_yoksa_indirim_uygulanmaz():
    t = table()
    assert t.effective_rate(Side.SELL, Liquidity.TAKER, has_discount_asset_balance=False) == \
        t.effective_rate(Side.SELL, Liquidity.TAKER)


def test_sembolde_kapaliysa_indirim_uygulanmaz():
    payload = dict(FAQ_PAYLOAD)
    payload["discount"] = dict(FAQ_PAYLOAD["discount"], enabledForSymbol=False)
    t = CommissionTable.from_api(payload)
    assert not t.discount.applies(has_discount_asset_balance=True)
