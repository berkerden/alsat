"""Kural deposu (Faz 2 → Faz 3 köprüsü) testleri.

Bu dosya deponun **gidiş-dönüşünü** korur: taramanın yazdığı her sayı,
arayüzün okuduğu nesnede birebir aynı çıkmalı. Bir alan yolda kaybolursa
kart eksik dolar ve kullanıcı bunu fark edemez.
"""
from __future__ import annotations

import json

import pytest

from albsat.strategy import rules as rulestore
from albsat.strategy.rules import (
    CostAssumptions,
    Rule,
    RuleEvidence,
    RuleSet,
    RunSummary,
    SectionSummary,
)


def evidence(**overrides) -> RuleEvidence:
    base = dict(
        olay=180, bagimsiz_olay=120, kabul_ornegi=62, isabet_orani=0.61,
        net_ortalama_yuzde=0.42, net_medyan_yuzde=0.38,
        guven_alt_yuzde=0.11, guven_ust_yuzde=0.73,
        p_degeri=0.000004, q_degeri=0.031, kabul_esigi_p=0.0000157,
        rastgeleyi_geciyor=True, rastgele_yuzdelik=0.97,
        donem_dogru_yon_orani=0.75, walk_forward_dogru_yon_orani=0.75,
        walk_forward_ortalama_yuzde=0.35, test_donemi_net_yuzde=0.29,
        hedefe_cikis_orani=0.61, stopa_cikis_orani=0.28,
        ortalama_tutulan_mum=2.4, mfe_medyan_yuzde=1.9, mae_medyan_yuzde=-0.8,
    )
    base.update(overrides)
    return RuleEvidence(**base)


def rule(**overrides) -> Rule:
    base = dict(
        sembol="BTCUSDT", periyot="15m", yon="al",
        ozellikler=("a", "b"), etiket="A + B",
        pencere_mum=3, hedef_atr=1.5, stop_atr=1.0, atr_periyodu=14,
        kanit=evidence(), uyarilar=("dikkat",), kabul=True, kabul_notu="",
    )
    base.update(overrides)
    return Rule(**base)


def cost() -> CostAssumptions:
    return CostAssumptions(
        maker_orani="0.001", taker_orani="0.001", spread_yuzde="0.01",
        kayma_yuzde="0.02", guvenlik_payi_yuzde="0.05", bnb_indirimi=False,
        minimum_hedef_yuzde="0.28", aciklama="en az %0.2800",
    )


def ruleset(**overrides) -> RuleSet:
    run = RunSummary(
        kosu_zamani_utc="2026-09-22T12:00:00+00:00",
        teshis_turu=False,
        semboller=("BTCUSDT",), periyotlar=("15m",), pencereler=(2, 3),
        toplam_aday=401, toplam_incelenen=401,
        alpha=0.10, kabul_esigi_p=0.0002494,
        veri_baslangic_utc="2025-02-19T00:00:00+00:00",
        veri_bitis_utc="2025-04-23T09:20:00+00:00",
        maliyet=cost(),
    )
    base = dict(
        kosu=run,
        kurallar=(rule(),),
        incelenen_adaylar=(rule(kabul=False, kabul_notu="geçmedi"),),
        bolumler=(
            SectionSummary(
                sembol="BTCUSDT", periyot="15m", pencere_mum=2,
                hedef_atr=1.5, stop_atr=1.0,
                uygun_mum=5748, aday=213, incelenen=213,
                taban_net_ortalama_yuzde=-0.2264, taban_isabet_orani=0.31,
                taban_olay=5748, en_iyi_ham_p=0.007481,
                kabul_esigi_p=0.0002494, kabul_edilen=0,
            ),
        ),
    )
    base.update(overrides)
    return RuleSet(**base)


# --- gidiş-dönüş ----------------------------------------------------------

def test_kaydet_yukle_her_alani_korur(tmp_path):
    original = ruleset()
    path = rulestore.save(original, tmp_path)
    geri = rulestore.load(path)

    assert geri.kosu == original.kosu
    assert geri.kurallar == original.kurallar
    assert geri.incelenen_adaylar == original.incelenen_adaylar
    assert geri.bolumler == original.bolumler


def test_kanitin_her_alani_yolda_kaybolmuyor(tmp_path):
    """Kanıt alanları tek tek karşılaştırılır; birinin unutulması kartı bozar."""
    original = ruleset()
    geri = rulestore.load(rulestore.save(original, tmp_path))
    once = original.kurallar[0].kanit
    sonra = geri.kurallar[0].kanit
    for alan in once.__dataclass_fields__:
        assert getattr(sonra, alan) == getattr(once, alan), alan


def test_dosya_adi_ve_yolu(tmp_path):
    assert rulestore.path_for(tmp_path).name == rulestore.DEFAULT_FILENAME
    rulestore.save(ruleset(), tmp_path)
    assert rulestore.path_for(tmp_path).exists()


def test_bicim_surumu_yazilir(tmp_path):
    path = rulestore.save(ruleset(), tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["surum"] == rulestore.FORMAT_VERSION


def test_gelecekteki_bicim_surumu_reddedilir(tmp_path):
    path = rulestore.save(ruleset(), tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["surum"] = rulestore.FORMAT_VERSION + 1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(rulestore.RuleStoreError):
        rulestore.load(path)


def test_bozuk_dosya_anlasilir_hata_verir(tmp_path):
    path = tmp_path / rulestore.DEFAULT_FILENAME
    path.write_text("{ bu json değil", encoding="utf-8")
    with pytest.raises(rulestore.RuleStoreError):
        rulestore.load(path)


# --- anlam taşıyan alanlar -------------------------------------------------

def test_kimlik_kuralin_tum_ayirt_edici_alanlarini_icerir():
    kimlik = rule().kimlik
    for parca in ("BTCUSDT", "15m", "3", "al", "a", "b"):
        assert parca in kimlik


def test_kimlik_farkli_kurallar_icin_farklidir():
    assert rule().kimlik != rule(pencere_mum=2).kimlik
    assert rule().kimlik != rule(yon="kacin").kimlik
    assert rule().kimlik != rule(ozellikler=("a", "c"), etiket="A + C").kimlik


def test_kabul_edilen_sayisi_yalnizca_kabulleri_sayar():
    rs = ruleset()
    assert rs.kabul_edilen_sayisi == 1
    assert len(rs.incelenen_adaylar) == 1
    assert all(not item.kabul for item in rs.incelenen_adaylar)


def test_esikten_uzaklik_kati():
    bolum = ruleset().bolumler[0]
    assert bolum.esikten_uzaklik_kati == pytest.approx(0.007481 / 0.0002494, rel=1e-9)


def test_incelenen_aday_notu_kabul_edilmedigini_soyluyor():
    metin = rulestore.NOT_ACCEPTED_NOTE.lower()
    assert "geçmedi" in metin or "edilmedi" in metin
    assert "öneri" in metin


# --- maliyet birimleri -----------------------------------------------------

def test_maliyet_oran_ve_yuzde_ayrimi():
    """``*_orani`` oran, ``*_yuzde`` yüzdedir. Karıştırmak 100 katlık hata."""
    c = cost()
    assert c.maker_orani == "0.001"
    assert float(c.maker_yuzde) == pytest.approx(0.1)
    assert float(c.taker_yuzde) == pytest.approx(0.1)


def test_maliyet_gidis_donusu_korunur(tmp_path):
    geri = rulestore.load(rulestore.save(ruleset(), tmp_path))
    assert geri.kosu.maliyet == cost()
