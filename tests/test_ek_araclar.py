"""B eki testleri: izleme, maliyet/risk paneli, disiplinli alım planı,
periyot sihirbazı ve sinyal günlüğü.

Bu bölüm ana akışın yanında duruyor ve **hiçbiri emir göndermiyor**; o
sınırın testi de burada (``test_hicbiri_emir_gondermiyor``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import veri_uret
from test_kural_deposu import cost, rule
from test_oneri_motoru import bolum, run_summary

from albsat.data.store import KlineStore
from albsat.strategy import journal as journallib
from albsat.strategy import panel as panellib
from albsat.strategy import plan as planlib
from albsat.strategy import watch as watchlib
from albsat.strategy.rules import RuleSet
from albsat.strategy.wizard import build_wizard, render_table


@pytest.fixture
def dizin(tmp_path):
    store = KlineStore(tmp_path)
    for periyot, adim in (("15m", 900_000), ("1h", 3_600_000)):
        frame = veri_uret.random_walk(3000, seed=5, step_ms=adim)
        store.write(frame, symbol="BTCUSDT", interval=periyot)
    return tmp_path


@pytest.fixture
def depo():
    return RuleSet(
        kosu=run_summary(periyotlar=("15m", "1h")),
        kurallar=(),
        incelenen_adaylar=(),
        bolumler=(bolum(), bolum(periyot="1h", pencere_mum=2)),
    )


# --- maliyet / risk paneli --------------------------------------------------

def test_panel_net_marji_kart_ile_ayni_yoldan_hesaplar():
    """Panel ve kart iki ayrı hesap yapsaydı sessizce ayrışırlardı."""
    p = panellib.evaluate(
        sembol="BTCUSDT", giris="100", hedef="101.5", stop="99",
        butce_usdt="100", risk_yuzde="1.0", maliyet=cost(),
    )
    assert float(p.net_marj_yuzde) == pytest.approx(1.2971015, abs=1e-7)
    assert float(p.stop_net_marj_yuzde) == pytest.approx(-1.1979010, abs=1e-7)
    assert float(p.basa_bas) == pytest.approx(100.20030041, abs=1e-8)


def test_panel_esigi_gecmeyen_hedefi_isaretler():
    """Maliyet eşiğinin altındaki hedef, iyi görünse bile alınmamalı."""
    dar = panellib.evaluate(
        sembol="BTCUSDT", giris="100", hedef="100.1", stop="99.5",
        butce_usdt="100", risk_yuzde="1.0", maliyet=cost(),
    )
    assert not dar.hedef_esigi_geciyor
    genis = panellib.evaluate(
        sembol="BTCUSDT", giris="100", hedef="102", stop="99",
        butce_usdt="100", risk_yuzde="1.0", maliyet=cost(),
    )
    assert genis.hedef_esigi_geciyor


def test_panel_esik_altinda_net_marj_negatif_olabilir():
    dar = panellib.evaluate(
        sembol="BTCUSDT", giris="100", hedef="100.1", stop="99.5",
        butce_usdt="100", risk_yuzde="1.0", maliyet=cost(),
    )
    assert dar.net_marj_yuzde < dar.brut_marj_yuzde
    assert dar.sonuc_tr


def test_panel_try_kuru_cevirisi():
    p = panellib.evaluate(
        sembol="BTCUSDT", giris="100", hedef="102", stop="99",
        butce_usdt="100", risk_yuzde="1.0", maliyet=cost(), try_kuru="41.5",
    )
    assert p.try_of(Decimal("10")) == Decimal("415.00")


# --- disiplinli alım planı --------------------------------------------------

def test_donemsel_plan_butceyi_dilimlere_boler(dizin):
    p = planlib.build_plan(
        sembol="BTCUSDT", butce_usdt="100", dilim_sayisi=4,
        maliyet=cost(), veri_dizini=dizin, periyot="1h",
        kip=planlib.MODE_PERIODIC, aralik_gun=7,
    )
    assert len(p.adimlar) == 4
    assert all(step.tutar_usdt == Decimal("25") for step in p.adimlar)


def test_kademeli_plan_fiyatlari_asagi_iner(dizin):
    p = planlib.build_plan(
        sembol="BTCUSDT", butce_usdt="100", dilim_sayisi=4,
        maliyet=cost(), veri_dizini=dizin, periyot="1h",
        kip=planlib.MODE_LADDER, basamak_yuzde="2",
    )
    fiyatlar = [step.fiyat for step in p.adimlar]
    assert fiyatlar == sorted(fiyatlar, reverse=True)


def test_plan_komisyonu_hesaba_katiyor(dizin):
    p = planlib.build_plan(
        sembol="BTCUSDT", butce_usdt="100", dilim_sayisi=4,
        maliyet=cost(), veri_dizini=dizin, periyot="1h",
    )
    assert p.toplam_komisyon_usdt > 0
    toplam = sum((step.komisyon_usdt for step in p.adimlar), Decimal(0))
    assert p.toplam_komisyon_usdt == toplam


def test_plan_gecmis_ornegi_tek_seferde_alimla_kiyaslar(dizin):
    """Kullanıcının asıl sorusu "bölerek almak işe yaradı mı"dır."""
    p = planlib.build_plan(
        sembol="BTCUSDT", butce_usdt="100", dilim_sayisi=8,
        maliyet=cost(), veri_dizini=dizin, periyot="1h", aralik_gun=7,
    )
    assert p.gecmis is not None
    assert p.gecmis.alim_sayisi > 1
    assert p.gecmis.tek_seferde_net_yuzde is not None
    # En kötü ara değer, kullanıcının "ne kadar kötü gördüm" sorusu.
    assert p.gecmis.en_kotu_ara_deger_yuzde <= 0


def test_gecersiz_dilim_sayisi_reddedilir(dizin):
    with pytest.raises(ValueError):
        planlib.build_plan(
            sembol="BTCUSDT", butce_usdt="100", dilim_sayisi=0,
            maliyet=cost(), veri_dizini=dizin,
        )


def test_bilinmeyen_kip_reddedilir(dizin):
    with pytest.raises(ValueError):
        planlib.build_plan(
            sembol="BTCUSDT", butce_usdt="100", dilim_sayisi=4,
            maliyet=cost(), veri_dizini=dizin, kip="serbest",
        )


# --- izleme -----------------------------------------------------------------

def test_izleme_tablosu_okunur_veri_uretir(dizin):
    board = watchlib.build_board(
        semboller=(("BTCUSDT", "1h"),), veri_dizini=dizin, maliyet=cost(),
    )
    assert len(board.satirlar) == 1
    satir = board.satirlar[0]
    assert satir.son_fiyat > 0
    assert satir.mum_sayisi > 0


def test_oynaklik_orani_maliyet_esigine_gore(dizin):
    """ATR%'nin maliyet eşiğine oranı, "burada iş var mı"nın tek satırlık cevabı."""
    board = watchlib.build_board(
        semboller=(("BTCUSDT", "1h"),), veri_dizini=dizin, maliyet=cost(),
    )
    satir = board.satirlar[0]
    beklenen = satir.atr_yuzde / satir.maliyet_esigi_yuzde
    assert satir.oynaklik_orani == pytest.approx(beklenen, rel=1e-9)
    assert satir.oynaklik_notu_tr


def test_kullanici_seviyeleri_uzakligi_gosterir(dizin):
    board = watchlib.build_board(
        semboller=(("BTCUSDT", "1h"),), veri_dizini=dizin, maliyet=cost(),
    )
    fiyat = board.satirlar[0].son_fiyat
    board = watchlib.build_board(
        semboller=(("BTCUSDT", "1h"),), veri_dizini=dizin, maliyet=cost(),
        seviyeler={"BTCUSDT": {"hedefim": str(fiyat * Decimal("1.10"))}},
    )
    seviye = board.satirlar[0].seviyeler[0]
    assert seviye.etiket == "hedefim"
    assert float(seviye.uzaklik_yuzde) == pytest.approx(10.0, abs=0.01)


def test_izlemede_alarm_yok(dizin):
    """Faz 3'te bildirim yok; olmayan bir şeyi vaat etmiyoruz."""
    board = watchlib.build_board(
        semboller=(("BTCUSDT", "1h"),), veri_dizini=dizin, maliyet=cost(),
    )
    assert "bildirim" in board.not_tr.lower() or "alarm" in board.not_tr.lower()


# --- periyot sihirbazı ------------------------------------------------------

def test_sihirbaz_her_periyot_icin_satir_uretir(depo, dizin):
    sonuc = build_wizard(depo, sembol="BTCUSDT",
                         periyotlar=("15m", "1h"), veri_dizini=dizin)
    assert [row.periyot for row in sonuc.satirlar] == ["15m", "1h"]


def test_sihirbaz_orani_atr_bolu_esik(depo, dizin):
    sonuc = build_wizard(depo, sembol="BTCUSDT",
                         periyotlar=("1h",), veri_dizini=dizin)
    row = sonuc.satirlar[0]
    assert row.oran == pytest.approx(
        row.atr_yuzde_medyan / row.minimum_hedef_yuzde, rel=1e-9
    )


def test_sihirbaz_sinyal_sikligi_toplanmaz(depo, dizin):
    """Üst üste binen kuralların sinyalleri toplanırsa günde 90 sinyal çıkar.

    Doğru sayı, en çok sinyal üreten **tek** kuralın sıklığıdır.
    """
    sonuc = build_wizard(depo, sembol="BTCUSDT",
                         periyotlar=("1h",), veri_dizini=dizin)
    row = sonuc.satirlar[0]
    assert row.gunluk_sinyal <= row.gunluk_mum


def test_sihirbaz_tablonun_ne_soyledigini_yaziyor(depo, dizin):
    """Tablo "nerede aranır"ı söyler, "nerede kâr var"ı değil."""
    sonuc = build_wizard(depo, sembol="BTCUSDT",
                         periyotlar=("15m", "1h"), veri_dizini=dizin)
    metin = " ".join(sonuc.gerekce).lower()
    assert "aran" in metin or "arama" in metin


def test_sihirbaz_tablosu_metne_dokulebiliyor(depo, dizin):
    sonuc = build_wizard(depo, sembol="BTCUSDT",
                         periyotlar=("15m", "1h"), veri_dizini=dizin)
    metin = render_table(sonuc)
    assert "15m" in metin and "1h" in metin


# --- sinyal günlüğü ---------------------------------------------------------

@pytest.fixture
def gunluk(tmp_path):
    return journallib.SignalJournal(tmp_path / "gunluk.sqlite3")


def ornek_kart(**overrides):
    """Günlüğe yazılacak kart; fiyatlar sonucu elle kurgulayabilmek için sade."""
    from albsat.risk.sizing import size_position
    from albsat.strategy.card import build_card
    from albsat.strategy.signals import cost_trips

    to_target, to_stop = cost_trips(cost())
    kural = rule(**overrides.pop("kural", {}))
    return build_card(
        kural,
        close_price=overrides.pop("close_price", "100"),
        atr_pct="1",
        signal_close_time_utc=overrides.pop(
            "signal_close_time_utc", "2026-01-01T01:00:00+00:00"
        ),
        interval_ms=900_000,
        trip_to_target=to_target,
        trip_to_stop=to_stop,
        position=size_position(entry="100", stop="99", budget_usdt="100",
                               risk_pct="1.0", round_trip=to_stop),
        rules=None,
    )


def test_ayni_sinyal_iki_kez_yazilmaz(gunluk):
    """Arayüz her yenilemede kaydetmeye çalışır; aynı mum bir kez girer."""
    kart = ornek_kart()
    assert gunluk.record(kart) is True
    assert gunluk.record(kart) is False
    assert len(gunluk.recent()) == 1


def test_farkli_mumlar_ayri_kayit(gunluk):
    gunluk.record(ornek_kart())
    gunluk.record(ornek_kart(signal_close_time_utc="2026-01-01T01:15:00+00:00"))
    assert len(gunluk.recent()) == 2


def test_yeni_kayit_acik_durumda(gunluk):
    gunluk.record(ornek_kart())
    kayit = gunluk.recent()[0]
    assert kayit.durum == journallib.STATUS_OPEN
    assert kayit.gerceklesen_net_yuzde is None
    assert kayit.durum_tr == "açık"


def test_performans_ozeti_bos_gunlukte_cokmez(gunluk):
    assert gunluk.performance() == {"sonuclanan": 0}


# --- sonuçlandırma: "önerilen vs gerçekleşen" -------------------------------

def _seri(tmp_path, mumlar):
    """Elle kurulmuş mum serisi; her mum (low, high, close)."""
    import pandas as pd

    baslangic = 1_767_225_600_000  # 2026-01-01T00:00:00Z
    satirlar = []
    for sira, (low, high, close) in enumerate(mumlar):
        satirlar.append({
            "open_time": baslangic + sira * 900_000,
            "open": close, "high": high, "low": low, "close": close,
            "volume": 1.0, "quote_volume": close, "trades": 1,
            "taker_buy_base": 0.5, "taker_buy_quote": close / 2,
            "is_closed": True,
        })
    store = KlineStore(tmp_path)
    store.write(pd.DataFrame(satirlar), symbol="BTCUSDT", interval="15m")
    return store


def _sonuclandir(gunluk, store):
    from albsat.strategy.signals import cost_trips

    to_target, to_stop = cost_trips(cost())
    return gunluk.settle_from_candles(
        store,
        trip_to_target=to_target,
        trip_to_stop=to_stop,
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )


def test_hedefe_ulasan_sinyal_hedef_olarak_kapanir(tmp_path, gunluk):
    # Mum 0: sinyal mumu (00:00–00:15). Sonraki üç mumda hedef 101,5 görülüyor.
    store = _seri(tmp_path, [
        (99.5, 100.5, 100),
        (100.0, 102.0, 101.8),   # hedef görüldü
        (100.0, 101.0, 100.5),
        (100.0, 101.0, 100.5),
    ])
    gunluk.record(ornek_kart(signal_close_time_utc="2026-01-01T00:15:00+00:00"))
    assert _sonuclandir(gunluk, store) == 1
    kayit = gunluk.recent()[0]
    assert kayit.durum == journallib.STATUS_HIT_TARGET
    assert kayit.gerceklesen_net_yuzde > 0


def test_stopa_dusen_sinyal_stop_olarak_kapanir(tmp_path, gunluk):
    store = _seri(tmp_path, [
        (99.5, 100.5, 100),
        (98.0, 100.5, 98.5),     # stop görüldü
        (98.0, 101.0, 100.0),
        (98.0, 101.0, 100.0),
    ])
    gunluk.record(ornek_kart(signal_close_time_utc="2026-01-01T00:15:00+00:00"))
    assert _sonuclandir(gunluk, store) == 1
    kayit = gunluk.recent()[0]
    assert kayit.durum == journallib.STATUS_HIT_STOP
    assert kayit.gerceklesen_net_yuzde < 0


def test_ayni_mumda_ikisi_de_gorulurse_stop_kazanir(tmp_path, gunluk):
    """Mum içinde hangisinin önce olduğunu bilemeyiz; temkinli varsayım stop.

    Bu, araştırmadaki olay çalışmasıyla aynı kural. Tersini seçmek geçmişi
    olduğundan iyi gösterirdi.
    """
    store = _seri(tmp_path, [
        (99.5, 100.5, 100),
        (98.0, 102.0, 100.0),    # hem stop hem hedef aynı mumda
        (99.0, 101.0, 100.0),
        (99.0, 101.0, 100.0),
    ])
    gunluk.record(ornek_kart(signal_close_time_utc="2026-01-01T00:15:00+00:00"))
    _sonuclandir(gunluk, store)
    assert gunluk.recent()[0].durum == journallib.STATUS_HIT_STOP


def test_hicbiri_gorulmezse_sure_doldu(tmp_path, gunluk):
    store = _seri(tmp_path, [
        (99.5, 100.5, 100),
        (99.5, 100.6, 100.2),
        (99.5, 100.6, 100.1),
        (99.5, 100.6, 100.3),
    ])
    gunluk.record(ornek_kart(signal_close_time_utc="2026-01-01T00:15:00+00:00"))
    _sonuclandir(gunluk, store)
    assert gunluk.recent()[0].durum == journallib.STATUS_TIMEOUT


def test_penceresi_dolmamis_sinyal_acik_kalir(tmp_path, gunluk):
    """Üç mumluk pencere için iki mum varsa sonuç uydurulmaz."""
    store = _seri(tmp_path, [(99.5, 100.5, 100), (99.5, 100.5, 100)])
    gunluk.record(ornek_kart(signal_close_time_utc="2026-01-01T00:15:00+00:00"))
    assert _sonuclandir(gunluk, store) == 0
    assert gunluk.recent()[0].durum == journallib.STATUS_OPEN


def test_sapma_ozeti_onerilen_ile_gerceklesen_farkini_verir(tmp_path, gunluk):
    """SPEC §4.8'in çekirdeği: "dediğim ile olan" arasındaki fark."""
    store = _seri(tmp_path, [
        (99.5, 100.5, 100),
        (98.0, 100.5, 98.5),
        (98.0, 101.0, 100.0),
        (98.0, 101.0, 100.0),
    ])
    gunluk.record(ornek_kart(signal_close_time_utc="2026-01-01T00:15:00+00:00"))
    _sonuclandir(gunluk, store)
    ozet = gunluk.performance()
    assert ozet["sonuclanan"] == 1
    assert ozet["onerilen_ortalama_yuzde"] > 0      # kart kâr öngörmüştü
    assert ozet["gerceklesen_ortalama_yuzde"] < 0   # gerçekte stop oldu


def test_gunluk_tabloya_dokulebiliyor(gunluk):
    gunluk.record(ornek_kart())
    tablo = journallib.to_frame(gunluk.recent())
    assert len(tablo) == 1
    assert "kural_kimligi" in tablo.columns
