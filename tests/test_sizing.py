"""Pozisyon büyüklüğü testleri (FAZ0 Risk #2).

Buradaki mesele tek cümleyle: kullanıcı "işlem başına %1 riske gireceğim"
dediğinde, 100 USDT'lik bütçede stepSize yuvarlaması yüzünden gerçekte
risk edilen tutar bambaşka çıkabilir. Kod bunu **gizlememeli**; hangi
kuralın (bütçe mi, risk mi) bağlayıcı olduğunu ve yuvarlamanın ne
götürdüğünü söylemeli.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from albsat.core.costs import round_trip_for
from albsat.core.fees import Liquidity, flat_table
from albsat.core.filters import SymbolRules
from albsat.risk.sizing import Binding, size_position

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
    ],
}


@pytest.fixture
def rules() -> SymbolRules:
    return SymbolRules.from_exchange_info(PAYLOAD)


@pytest.fixture
def trip():
    table = flat_table("*", "0.001", "0.001")
    return round_trip_for(
        table, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )


def test_risk_baglayici_oldugunda_miktar_riskten_gelir(trip):
    """Geniş stop mesafesi: sınırı risk kuralı koyar, bütçe artar."""
    size = size_position(
        entry="100", stop="95", budget_usdt="10000", risk_pct="1.0",
        round_trip=trip,
    )
    assert size.baglayici is Binding.RISK
    # Hedeflenen risk 100 USDT, mesafe 5 USDT → 20 adet, 2000 USDT tutar.
    assert size.miktar == Decimal("20")
    assert size.tutar_usdt == Decimal("2000")
    assert size.butce_kullanim_yuzde < Decimal("100")


def test_esitlikte_baglayici_butce_sayilir(trip):
    """Risk ve bütçe aynı miktarı verirse "bütçenin tamamı kullanılıyor"
    demek doğru olandır: kullanıcının elinde başka para kalmıyor."""
    size = size_position(
        entry="100", stop="99", budget_usdt="10000", risk_pct="1.0",
        round_trip=trip,
    )
    assert size.baglayici is Binding.BUDGET
    assert size.butce_kullanim_yuzde == pytest.approx(Decimal("100"), rel=1e-9)


def test_butce_baglayici_oldugunda_uyari_cikar(trip):
    """Küçük bütçe: risk kuralının istediği miktar alınamaz."""
    size = size_position(
        entry="100", stop="99", budget_usdt="100", risk_pct="1.0",
        round_trip=trip,
    )
    assert size.baglayici is Binding.BUDGET
    assert size.miktar == Decimal("1")
    assert "bütçe" in size.aciklama_tr.lower()


def test_baglayici_kart_metninde_yaziyor(trip):
    """Kullanıcı hangi kuralın bağladığını okumadan karar vermemeli."""
    for stop, beklenen in (("95", "risk"), ("99.9", "bütçe")):
        size = size_position(
            entry="100", stop=stop, budget_usdt="10000", risk_pct="1.0",
            round_trip=trip,
        )
        assert beklenen in size.aciklama_tr.lower()


def test_stepsize_yuvarlamasi_asagi_yapilir(rules, trip):
    """Yukarı yuvarlamak hedeflenenden fazla risk aldırırdı."""
    size = size_position(
        entry="100", stop="99", budget_usdt="10000", risk_pct="0.0123456789",
        round_trip=trip, rules=rules,
    )
    assert size.miktar <= size.ham_miktar
    # stepSize 0.00001 → beş basamaktan fazlası kalmamalı.
    assert size.miktar == size.miktar.quantize(Decimal("0.00001"))


def test_yuvarlama_kaybi_raporlaniyor(rules, trip):
    size = size_position(
        entry="100", stop="99", budget_usdt="10000", risk_pct="0.0123456789",
        round_trip=trip, rules=rules,
    )
    assert size.yuvarlama_kaybi_yuzde > 0
    beklenen = (size.ham_miktar - size.miktar) / size.ham_miktar * 100
    assert size.yuvarlama_kaybi_yuzde == pytest.approx(beklenen, rel=1e-9)


def test_stop_zarari_komisyonu_iceriyor(trip):
    """Stop zararı yalnızca fiyat farkı değil; iki komisyon da ödenir."""
    size = size_position(
        entry="100", stop="99", budget_usdt="100", risk_pct="1.0",
        round_trip=trip,
    )
    fiyat_farki = Decimal("1")  # 1 adet × (100 − 99)
    assert size.stop_zarari_usdt > fiyat_farki
    assert size.gerceklesen_risk_yuzde > Decimal("1.0")


def test_gerceklesen_risk_hedeflenenden_sapinca_uyari_var(trip):
    size = size_position(
        entry="100", stop="99", budget_usdt="100", risk_pct="1.0",
        round_trip=trip,
    )
    assert any("risk" in u.lower() for u in size.uyarilar)


def test_notional_altinda_kalan_pozisyon_gecersiz(rules, trip):
    """minNotional 5 USDT; 1 USDT'lik bütçeyle emir gönderilemez."""
    size = size_position(
        entry="100", stop="99", budget_usdt="1", risk_pct="1.0",
        round_trip=trip, rules=rules,
    )
    assert not size.gecerli
    # Uyarı hem neyin yetmediğini hem de neyin gerektiğini söylemeli.
    metin = " ".join(size.uyarilar).lower()
    assert "en düşük tutar" in metin
    assert "en küçük geçerli emir" in metin
    assert size.asgari_gecerli_miktar == Decimal("0.05000")


def test_stop_girisin_ustundeyse_hesap_yapilmaz(trip):
    """Ters stop sessizce bir sayı üretmemeli; anlaşılır Türkçe hata verir.

    Arayüz bu metni kullanıcıya olduğu gibi gösteriyor (``/api/maliyet-risk``
    400 döner), o yüzden mesajın Türkçe ve yol gösterici olması testli.
    """
    with pytest.raises(ValueError, match="Stop, giriş fiyatının altında"):
        size_position(
            entry="100", stop="101", budget_usdt="100", risk_pct="1.0",
            round_trip=trip,
        )


def test_giris_sifir_veya_negatifse_hata(trip):
    with pytest.raises(ValueError):
        size_position(
            entry="0", stop="-1", budget_usdt="100", risk_pct="1.0",
            round_trip=trip,
        )


def test_butce_kullanimi_yuzde_olarak_veriliyor(trip):
    size = size_position(
        entry="100", stop="99", budget_usdt="100", risk_pct="1.0",
        round_trip=trip,
    )
    assert size.butce_kullanim_yuzde == pytest.approx(Decimal("100"), rel=1e-9)
