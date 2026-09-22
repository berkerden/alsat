"""Öneri kartı testleri: SPEC.md §10'un "marjlar komisyon sonrası doğru"
kabul kriterinin karşılığı.

Sayılar ``albsat.strategy.example`` içindeki örnek kuraldan geliyor; o kural
elle hesaplanabilsin diye yuvarlak seçildi (fiyat 100, ATR %1, komisyon
%0,1/%0,1). Beklenen değerler kod okunarak değil, kâğıt üstünde çıkarıldı::

    brüt marj = (101,5 − 100) / 100                = %1,5000000
    net marj  = 101,5 · (1−0,001)(1−0,001) − 100   = %1,2971015
    başa-baş  = 100 / ((1−0,001)(1−0,001))         = 100,20030041
    stop net  = 99 · (1−0,001)(1−0,001) − 100      = %−1,1979010
    hedef 2   = MFE %1,9 → 101,9                   (net %1,6963019)

Bu dosya bozulursa kart yanlış sayı gösteriyor demektir.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from albsat.core.filters import SymbolRules
from albsat.strategy.card import DISCLAIMER, ENTRY_ASSUMPTION_NOTE, build_card, confidence_for
from albsat.strategy.example import (
    EXAMPLE_COST,
    EXAMPLE_NOTE,
    EXAMPLE_RULE,
    example_card,
    example_recommendation,
)
from albsat.strategy.rules import Rule
from albsat.strategy.signals import STATUS_EXAMPLE, cost_trips

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
def kart():
    return example_card()


# --- elle doğrulanmış aritmetik --------------------------------------------

def test_fiyat_seviyeleri(kart):
    assert kart.giris == Decimal("100")
    assert kart.hedef1 == Decimal("101.5")   # 1,5 × ATR %1
    assert kart.stop == Decimal("99")        # 1,0 × ATR %1


def test_brut_marj(kart):
    assert float(kart.brut_marj_yuzde) == pytest.approx(1.5, abs=1e-9)


def test_net_marj_komisyon_sonrasi(kart):
    assert float(kart.net_marj_yuzde) == pytest.approx(1.2971015, abs=1e-7)


def test_net_marj_brutten_kucuk(kart):
    """Komisyon her zaman marjı küçültür; tersi bir işaret hatasıdır."""
    assert kart.net_marj_yuzde < kart.brut_marj_yuzde


def test_basa_bas_fiyati(kart):
    assert float(kart.basa_bas) == pytest.approx(100.20030041, abs=1e-8)


def test_basa_bas_giristen_yuksek(kart):
    """Girişte de çıkışta da komisyon ödendiği için başa-baş girişin üstünde."""
    assert kart.basa_bas > kart.giris


def test_stop_net_marji(kart):
    assert float(kart.stop_net_marj_yuzde) == pytest.approx(-1.1979010, abs=1e-7)


def test_stopta_zarar_fiyat_farkindan_buyuk(kart):
    """Stop %1 aşağıda ama komisyonla birlikte kayıp %1'den fazla."""
    assert kart.stop_net_marj_yuzde < Decimal("-1")


def test_hedef2_olculmus_mfeden_gelir(kart):
    assert kart.hedef2 == Decimal("101.9")   # MFE medyanı %1,9
    assert float(kart.hedef2_net_marj_yuzde) == pytest.approx(1.6963019, abs=1e-7)


def test_risk_odul_net_marjlardan_hesaplanir(kart):
    beklenen = kart.net_marj_yuzde / -kart.stop_net_marj_yuzde
    assert float(kart.risk_odul) == pytest.approx(float(beklenen), abs=1e-2)


# --- uydurulmayan alanlar ---------------------------------------------------

def test_mfe_hedef1in_altindaysa_hedef2_yok():
    """Ölçüm desteklemiyorsa ikinci hedef konmaz; sebebi kartta yazar."""
    zayif = Rule(
        sembol=EXAMPLE_RULE.sembol, periyot=EXAMPLE_RULE.periyot,
        yon=EXAMPLE_RULE.yon, ozellikler=EXAMPLE_RULE.ozellikler,
        etiket=EXAMPLE_RULE.etiket, pencere_mum=EXAMPLE_RULE.pencere_mum,
        hedef_atr=EXAMPLE_RULE.hedef_atr, stop_atr=EXAMPLE_RULE.stop_atr,
        atr_periyodu=EXAMPLE_RULE.atr_periyodu,
        # MFE hedefin (%1,5) altında:
        kanit=type(EXAMPLE_RULE.kanit)(
            **{**EXAMPLE_RULE.kanit.__dict__, "mfe_medyan_yuzde": 0.9}
        ),
        uyarilar=(), kabul=True, kabul_notu="",
    )
    to_target, to_stop = cost_trips(EXAMPLE_COST)
    from albsat.risk.sizing import size_position

    card = build_card(
        zayif, close_price="100", atr_pct="1",
        signal_close_time_utc="2026-01-01T00:00:00+00:00",
        interval_ms=3_600_000,
        trip_to_target=to_target, trip_to_stop=to_stop,
        position=size_position(entry="100", stop="99", budget_usdt="100",
                               risk_pct="1.0", round_trip=to_stop),
        rules=None,
    )
    assert card.hedef2 is None
    assert card.hedef2_net_marj_yuzde is None
    assert "ikinci hedef" in card.hedef2_notu.lower()


def test_iki_giris_varsayimi_da_kartta_yaziyor(kart):
    """Araştırma piyasa emriyle girdi, kart limit öneriyor; fark gizlenmez."""
    assert ENTRY_ASSUMPTION_NOTE in kart.gerekce


def test_filtre_yokken_uyari_var(kart):
    assert not kart.filtreler_uygulandi
    assert any("tickstep" in u.lower() or "tickSize" in u for u in kart.uyarilar)


def test_filtre_varken_fiyatlar_yuvarlanir():
    rules = SymbolRules.from_exchange_info(PAYLOAD)
    card = example_card(rules=rules)
    assert card.filtreler_uygulandi
    for fiyat in (card.giris, card.hedef1, card.stop):
        assert fiyat == fiyat.quantize(Decimal("0.01"))


def test_gecerlilik_penceresi_kural_penceresi_kadar(kart):
    # Sinyal mumu 2026-01-01 00:00 UTC, periyot 1h, pencere 3 mum → 03:00.
    assert kart.gecerlilik_mum == 3
    assert kart.gecerlilik_bitis_utc.startswith("2026-01-01T03:00")


def test_istanbul_saati_utc_arti_uc(kart):
    assert kart.gecerlilik_bitis_istanbul.endswith("06:00")


def test_try_kuru_verilmezse_try_alanlari_bos(kart):
    assert kart.try_kuru is None
    assert kart.try_of(Decimal("100")) is None


def test_try_kuru_verilince_cevrim_yapilir():
    to_target, to_stop = cost_trips(EXAMPLE_COST)
    from albsat.risk.sizing import size_position

    card = build_card(
        EXAMPLE_RULE, close_price="100", atr_pct="1",
        signal_close_time_utc="2026-01-01T00:00:00+00:00",
        interval_ms=3_600_000,
        trip_to_target=to_target, trip_to_stop=to_stop,
        position=size_position(entry="100", stop="99", budget_usdt="100",
                               risk_pct="1.0", round_trip=to_stop),
        rules=None, try_rate="41.5",
    )
    assert card.try_of(Decimal("100")) == Decimal("4150.00")


# --- güven skoru ------------------------------------------------------------

def test_guven_bilesenleri_yuze_tamamlanir():
    skor = confidence_for(EXAMPLE_RULE)
    assert sum(b.azami for b in skor.bilesenler) == 100


def test_guven_puani_sifir_ile_yuz_arasinda():
    skor = confidence_for(EXAMPLE_RULE)
    assert 0 <= skor.puan <= 100


def test_uyarilar_guven_puanini_dusurur():
    temiz = confidence_for(EXAMPLE_RULE)
    uyarili = confidence_for(
        Rule(**{**EXAMPLE_RULE.__dict__, "uyarilar": ("a", "b")})
    )
    assert uyarili.puan < temiz.puan
    assert uyarili.ceza > 0


def test_az_ornekli_kural_daha_dusuk_puan_alir():
    az = Rule(**{**EXAMPLE_RULE.__dict__,
                 "kanit": type(EXAMPLE_RULE.kanit)(
                     **{**EXAMPLE_RULE.kanit.__dict__, "kabul_ornegi": 12})})
    assert confidence_for(az).puan < confidence_for(EXAMPLE_RULE).puan


def test_kartta_kuralin_kaniti_tasiniyor(kart):
    """SPEC §4.4: kartta "tarihsel isabet oranı (n=...)" olmalı.

    Arayüz n olarak ``olay``ı yazıyor, çünkü isabet oranı o örneklem
    üzerinde ölçüldü; ``bagimsiz_olay`` ayrıca gösteriliyor. İkisini
    karıştırmak, 180 olayda ölçülen oranı 120 olaya dayanıyormuş gibi
    göstermek olurdu.
    """
    assert kart.kanit.isabet_orani == EXAMPLE_RULE.kanit.isabet_orani
    assert kart.kanit.olay == EXAMPLE_RULE.kanit.olay
    assert kart.kanit.bagimsiz_olay == EXAMPLE_RULE.kanit.bagimsiz_olay
    assert kart.kanit.olay >= kart.kanit.bagimsiz_olay


# --- örnek kart bir öneri değildir ------------------------------------------

def test_ornek_kart_oneri_olmadigini_soyluyor(kart):
    assert any(u == EXAMPLE_NOTE for u in kart.uyarilar)
    assert "öneri değildir" in EXAMPLE_NOTE


def test_ornek_cikti_kendi_durum_koduyla_gelir():
    sonuc = example_recommendation()
    assert sonuc.durum == STATUS_EXAMPLE
    assert sonuc.kabul_edilen_kural == 0


def test_uyari_metni_yatirim_tavsiyesi_degil():
    assert "yatırım tavsiyesi değildir" in DISCLAIMER
