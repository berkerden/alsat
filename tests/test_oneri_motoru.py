"""Öneri motoru testleri.

En önemlisi ilk bölüm: **kabul edilmiş kural yokken** motor uydurma kart
üretmemeli, "önerilecek kural yok" deyip sebebini sayılarla açıklamalı.
Faz 2 bu kapsamda kural bulamadı; kullanıcının göreceği ekran budur ve
"arıza mı, sonuç mu" sorusunun cevabı orada yazılı olmalı.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import veri_uret
from test_kural_deposu import cost, rule  # noqa: F401  (aynı kurgu)

from albsat.data.klines import to_utc
from albsat.data.store import KlineStore
from albsat.strategy.rules import RuleSet, RunSummary, SectionSummary
from albsat.strategy.signals import (
    STATUS_DIAGNOSTIC,
    STATUS_HAS_SIGNAL,
    STATUS_NO_DATA,
    STATUS_NO_RULES,
    STATUS_NOT_TRIGGERED,
    cost_trips,
    recommend,
)


def run_summary(**overrides) -> RunSummary:
    base = dict(
        kosu_zamani_utc="2026-09-22T12:00:00+00:00",
        teshis_turu=False,
        semboller=("BTCUSDT",), periyotlar=("15m",), pencereler=(2, 3),
        toplam_aday=401, toplam_incelenen=401,
        alpha=0.10, kabul_esigi_p=0.0002494,
        veri_baslangic_utc="2025-02-19T00:00:00+00:00",
        veri_bitis_utc="2025-04-23T09:20:00+00:00",
        maliyet=cost(),
    )
    base.update(overrides)
    return RunSummary(**base)


def bolum(**overrides) -> SectionSummary:
    base = dict(
        sembol="BTCUSDT", periyot="15m", pencere_mum=2,
        hedef_atr=1.5, stop_atr=1.0,
        uygun_mum=5748, aday=213, incelenen=213,
        taban_net_ortalama_yuzde=-0.2264, taban_isabet_orani=0.31,
        taban_olay=5748, en_iyi_ham_p=0.007481,
        kabul_esigi_p=0.0002494, kabul_edilen=0,
    )
    base.update(overrides)
    return SectionSummary(**base)


@pytest.fixture
def veri(tmp_path):
    """15m rastgele yürüyüş — içinde bilerek örüntü yok."""
    store = KlineStore(tmp_path)
    frame = veri_uret.random_walk(900, seed=5)
    store.write(frame, symbol="BTCUSDT", interval="15m")
    son = to_utc(int(frame["open_time"].max()) + 900_000)
    return tmp_path, son


def bos_depo() -> RuleSet:
    return RuleSet(
        kosu=run_summary(),
        kurallar=(),
        incelenen_adaylar=(rule(kabul=False, kabul_notu="geçmedi"),),
        bolumler=(bolum(), bolum(pencere_mum=3, en_iyi_ham_p=0.0224, aday=188)),
    )


# --- kural yokken -----------------------------------------------------------

def test_kural_yoksa_kart_uretilmez(veri):
    dizin, son = veri
    sonuc = recommend(bos_depo(), sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert sonuc.durum == STATUS_NO_RULES
    assert sonuc.kartlar == ()
    assert sonuc.kabul_edilen_kural == 0


def test_kural_yoksa_sebep_sayilarla_aciklanir(veri):
    dizin, son = veri
    sonuc = recommend(bos_depo(), sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    metin = " ".join(sonuc.aciklama.satirlar)
    assert "401" in metin                 # kaç aday denendi
    assert "0.0002494" in metin           # kabul eşiği
    assert "30 katı" in metin             # en yakın adayın uzaklığı
    assert "%-0.2264" in metin or "-0.2264" in metin   # taban çizgisi


def test_kural_yoksa_bunun_ariza_olmadigi_yaziyor(veri):
    """Kullanıcı bu ekranı görünce "bozuldu mu?" diye sormamalı."""
    dizin, son = veri
    sonuc = recommend(bos_depo(), sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    metin = " ".join(sonuc.aciklama.satirlar).lower()
    assert "arıza değil" in metin
    assert "ölçülmüş" in metin


def test_kural_yokken_incelenen_adaylar_karta_donusmez(veri):
    """Eşiği geçmemiş adaylar öneri gibi gösterilmez (bilinçli karar)."""
    dizin, son = veri
    depo = bos_depo()
    assert len(depo.incelenen_adaylar) == 1
    sonuc = recommend(depo, sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert sonuc.kartlar == ()


def test_kural_yoksa_taranan_bolumler_tabloya_giriyor(veri):
    dizin, son = veri
    sonuc = recommend(bos_depo(), sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert len(sonuc.aciklama.bolumler) == 2


# --- teşhis deposu ----------------------------------------------------------

def test_teshis_deposundan_oneri_cikmaz(veri):
    """Maliyeti sıfır sayan teşhis turu işlem önerisi üretemez."""
    dizin, son = veri
    depo = RuleSet(
        kosu=run_summary(teshis_turu=True),
        kurallar=(rule(),),           # kabul edilmiş kural olsa bile
        incelenen_adaylar=(), bolumler=(bolum(),),
    )
    sonuc = recommend(depo, sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert sonuc.durum == STATUS_DIAGNOSTIC
    assert sonuc.kartlar == ()


# --- veri durumu ------------------------------------------------------------

def test_veri_yoksa_veri_yok_denir(tmp_path):
    sonuc = recommend(bos_depo(), sembol="BTCUSDT", periyot="15m",
                      veri_dizini=tmp_path)
    assert sonuc.durum == STATUS_NO_DATA
    assert sonuc.kartlar == ()


def test_bayat_veri_isaretlenir(veri):
    """Son kapanışın çok gerisinde kalan veriyle öneri yanıltıcıdır."""
    dizin, _ = veri
    cok_sonra = datetime(2030, 1, 1, tzinfo=UTC)
    sonuc = recommend(bos_depo(), sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=cok_sonra)
    assert sonuc.veri.bayat
    assert "tazele" in sonuc.veri.aciklama_tr.lower()


def test_guncel_veri_bayat_degil(veri):
    dizin, son = veri
    sonuc = recommend(bos_depo(), sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert not sonuc.veri.bayat


# --- kural varken -----------------------------------------------------------

def test_kural_var_ama_tetiklenmediyse_kart_yok(veri):
    """Tetiklenmeyen kural için kart üretmek, olmayan sinyali göstermektir."""
    dizin, son = veri
    depo = RuleSet(
        kosu=run_summary(),
        kurallar=(rule(ozellikler=("olmayan_ozellik",), etiket="Olmayan"),),
        incelenen_adaylar=(), bolumler=(bolum(),),
    )
    sonuc = recommend(depo, sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert sonuc.durum == STATUS_NOT_TRIGGERED
    assert sonuc.kartlar == ()
    assert sonuc.kabul_edilen_kural == 1


def test_baska_sembolun_kurali_kullanilmaz(veri):
    dizin, son = veri
    depo = RuleSet(
        kosu=run_summary(),
        kurallar=(rule(sembol="SOLUSDT"),),
        incelenen_adaylar=(), bolumler=(bolum(),),
    )
    sonuc = recommend(depo, sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert sonuc.kabul_edilen_kural == 0


def test_baska_periyodun_kurali_kullanilmaz(veri):
    dizin, son = veri
    depo = RuleSet(
        kosu=run_summary(),
        kurallar=(rule(periyot="1h"),),
        incelenen_adaylar=(), bolumler=(bolum(),),
    )
    sonuc = recommend(depo, sembol="BTCUSDT", periyot="15m",
                      veri_dizini=dizin, now=son)
    assert sonuc.kabul_edilen_kural == 0


def test_tetiklenen_kural_kart_uretir(tmp_path):
    """Her mumda doğru olan bir koşulla kartın gerçekten üretildiğini gösterir."""
    store = KlineStore(tmp_path)
    frame = veri_uret.random_walk(900, seed=5)
    store.write(frame, symbol="BTCUSDT", interval="15m")
    son = to_utc(int(frame["open_time"].max()) + 900_000)

    her_zaman = _her_zaman_dogru_ozellik()
    depo = RuleSet(
        kosu=run_summary(),
        kurallar=(rule(ozellikler=(her_zaman,), etiket="Her zaman doğru"),),
        incelenen_adaylar=(), bolumler=(bolum(),),
    )
    sonuc = recommend(depo, sembol="BTCUSDT", periyot="15m",
                      veri_dizini=tmp_path, now=son)
    assert sonuc.durum == STATUS_HAS_SIGNAL
    assert len(sonuc.kartlar) == 1
    kart = sonuc.kartlar[0]
    assert kart.giris > 0
    assert kart.hedef1 > kart.giris
    assert kart.stop < kart.giris
    assert kart.net_marj_yuzde < kart.brut_marj_yuzde


def _her_zaman_dogru_ozellik() -> str:
    """Seride her mumda doğru olan bir özellik adı bulur.

    Özellik adlarını teste sabit yazmak, özellik kümesi değişince testi
    sessizce anlamsızlaştırırdı; bunun yerine veriden seçiliyor.
    """

    from albsat.features import build_features

    frame = veri_uret.random_walk(900, seed=5)
    ozellikler = build_features(frame, interval="15m")
    son = len(ozellikler.frame) - 1
    for ad in ozellikler.frame.columns:
        seri = ozellikler.frame[ad].to_numpy()
        if seri.dtype == bool and bool(seri[son]):
            return str(ad)
    pytest.skip("Son mumda doğru olan bir özellik bulunamadı")


# --- maliyet eşlemesi -------------------------------------------------------

def test_maliyet_turlari_arastirmayla_ayni():
    """Giriş taker, hedefe çıkış maker, stopa çıkış taker (FAZ0 Risk #4)."""
    from albsat.core.fees import Liquidity

    hedefe, stopa = cost_trips(cost())
    assert hedefe.entry.liquidity is Liquidity.TAKER
    assert hedefe.exit.liquidity is Liquidity.MAKER
    assert stopa.entry.liquidity is Liquidity.TAKER
    assert stopa.exit.liquidity is Liquidity.TAKER


def test_maliyet_oranlari_yuzde_olarak_okunmuyor():
    """``maker_orani`` 0,001 ise komisyon %0,1'dir; %0,001 değil."""
    hedefe, _ = cost_trips(cost())
    assert float(hedefe.entry.rate_pct) == pytest.approx(0.1)
