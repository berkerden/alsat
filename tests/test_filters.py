"""exchangeInfo filtre testleri."""
from decimal import Decimal

import pytest

from albsat.core.fees import Side
from albsat.core.filters import SymbolRules, parse_exchange_info

PAYLOAD = {
    "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
    "status": "TRADING", "ocoAllowed": True, "otoAllowed": True,
    "allowTrailingStop": True, "isSpotTradingAllowed": True,
    "pegInstructionsAllowed": True, "amendAllowed": True,
    "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01000000",
         "maxPrice": "1000000.00000000", "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "minQty": "0.00001000",
         "maxQty": "9000.00000000", "stepSize": "0.00001000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000",
         "applyMinToMarket": True, "maxNotional": "9000000.00000000",
         "applyMaxToMarket": False, "avgPriceMins": 5},
        {"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierUp": "5",
         "bidMultiplierDown": "0.2", "askMultiplierUp": "5",
         "askMultiplierDown": "0.2", "avgPriceMins": 5},
    ],
}


@pytest.fixture
def rules():
    return SymbolRules.from_exchange_info(PAYLOAD)


def test_alis_fiyati_asagi_satis_yukari_yuvarlanir(rules):
    # Aleyhimize yuvarlamak, küçük hareketlerde marjı yer.
    assert rules.round_price("43210.567", Side.BUY) == Decimal("43210.56")
    assert rules.round_price("43210.561", Side.SELL) == Decimal("43210.57")


def test_miktar_daima_asagi_yuvarlanir(rules):
    # Yukarı yuvarlamak, risk motorunun izin verdiğinden büyük pozisyon demek.
    assert rules.round_quantity("0.000123456") == Decimal("0.00012")


def test_gecerli_emir_ihlal_uretmez(rules):
    assert rules.validate_limit_order(
        side=Side.BUY, price="43000.00", quantity="0.00020000"
    ) == []


def test_min_notional_altindaki_emir_yakalanir(rules):
    violations = rules.validate_limit_order(
        side=Side.BUY, price="43000.00", quantity="0.00010000"
    )
    assert [v.filter_type for v in violations] == ["NOTIONAL"]
    assert "en düşük tutarın" in violations[0].message


def test_tick_disi_fiyat_yakalanir(rules):
    violations = rules.validate_limit_order(
        side=Side.BUY, price="43000.005", quantity="0.00020000"
    )
    assert any(v.field == "tickSize" for v in violations)


def test_percent_price_by_side_referans_fiyata_gore_calisir(rules):
    # Referans 43000 iken bid üst sınırı 43000*5, alt sınırı 43000*0.2
    violations = rules.validate_limit_order(
        side=Side.BUY, price="1000.00", quantity="1.00000000",
        reference_price="43000",
    )
    assert any(v.filter_type == "PERCENT_PRICE_BY_SIDE" for v in violations)


def test_min_quantity_for_notional_gercekten_gecerlidir(rules):
    price = "43000.00"
    qty = rules.min_quantity_for_notional(price)
    assert rules.validate_limit_order(side=Side.BUY, price=price, quantity=qty) == []


def test_cancel_only_durumunda_islem_yapilmaz():
    # 2026-07-07 changelog: yeni symbolStatus değeri
    payload = dict(PAYLOAD, status="CANCEL_ONLY")
    rules = SymbolRules.from_exchange_info(payload)
    assert not rules.tradable
    violations = rules.validate_limit_order(
        side=Side.BUY, price="43000.00", quantity="0.00020000"
    )
    assert any(v.filter_type == "SYMBOL_STATUS" for v in violations)


def test_otoco_her_iki_izni_de_gerektirir():
    assert SymbolRules.from_exchange_info(PAYLOAD).otoco_allowed
    kisitli = SymbolRules.from_exchange_info(dict(PAYLOAD, ocoAllowed=False))
    assert not kisitli.otoco_allowed


def test_eski_min_notional_adi_da_okunur():
    payload = dict(PAYLOAD)
    payload["filters"] = [
        f for f in PAYLOAD["filters"] if f["filterType"] != "NOTIONAL"
    ] + [{"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000",
          "applyToMarket": True, "avgPriceMins": 5}]
    rules = SymbolRules.from_exchange_info(payload)
    assert rules.notional is not None
    assert rules.notional.min_notional == Decimal("10.00000000")


def test_parse_exchange_info_sozluk_dondurur():
    parsed = parse_exchange_info({"symbols": [PAYLOAD]})
    assert set(parsed) == {"BTCUSDT"}
