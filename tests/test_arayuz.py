"""Arayüz uçlarının testleri (FastAPI TestClient).

İki şeyi koruyorlar:

1. **Sayı biçimi.** Parasal her değer arayüze *metin* olarak gider. ``float``
   parasal değerlerde yasak (SPEC §3) ve ``str(Decimal)`` küçük sayılarda
   ``9.9E-7`` üretir; kullanıcı bunu okuyamaz. Test, gelen değerin metin
   olduğunu ve bilimsel gösterim içermediğini doğruluyor.
2. **Mod.** Uygulama "Sadece Öneri" modunda açılıyor; Binance'e emir
   gönderen bir uç yok (kâğıt emir uçları yalnızca ``/api/kagit/`` altında,
   ayrıntısı ``test_kagit_arayuz.py``'de), API anahtarı kullanılmıyor ve
   sunucu yalnızca 127.0.0.1'e bağlanıyor.
"""
from __future__ import annotations

import json

import pytest
import veri_uret
from fastapi.testclient import TestClient
from test_kural_deposu import rule
from test_oneri_motoru import bolum, run_summary

from albsat.api.app import MODE, MODE_TR, AppState, create_app
from albsat.cli.serve import HOST
from albsat.data.store import KlineStore
from albsat.strategy import rules as rulestore
from albsat.strategy.rules import RuleSet


def _veri(tmp_path):
    store = KlineStore(tmp_path)
    for periyot, adim in (("15m", 900_000), ("1h", 3_600_000)):
        store.write(
            veri_uret.random_walk(2000, seed=5, step_ms=adim),
            symbol="BTCUSDT", interval=periyot,
        )
    return store


def _depo(tmp_path, *, kurallar=()):
    depo = RuleSet(
        kosu=run_summary(periyotlar=("15m", "1h")),
        kurallar=kurallar,
        incelenen_adaylar=(rule(kabul=False, kabul_notu="geçmedi"),),
        bolumler=(bolum(), bolum(periyot="1h", pencere_mum=2)),
    )
    rulestore.save(depo, tmp_path)
    return depo


@pytest.fixture
def client(tmp_path):
    _veri(tmp_path)
    _depo(tmp_path)
    state = AppState(
        veri_dizini=tmp_path, butce_usdt="100", islem_basi_risk_yuzde="1.0",
        semboller=("BTCUSDT",), periyotlar=("15m", "1h"),
    )
    return TestClient(create_app(state), base_url="http://127.0.0.1")


@pytest.fixture
def bos_client(tmp_path):
    """Kural deposu hiç olmayan kurulum."""
    _veri(tmp_path)
    state = AppState(
        veri_dizini=tmp_path, butce_usdt="100", islem_basi_risk_yuzde="1.0",
        semboller=("BTCUSDT",), periyotlar=("15m",),
    )
    return TestClient(create_app(state), base_url="http://127.0.0.1")


# --- her uç ayakta ----------------------------------------------------------

UCLAR = [
    ("/api/durum", {}),
    ("/api/oneriler", {"sembol": "BTCUSDT", "periyot": "15m"}),
    ("/api/oneriler", {"sembol": "BTCUSDT", "periyot": "15m", "ornek": "true"}),
    ("/api/kurallar", {}),
    ("/api/sihirbaz", {"sembol": "BTCUSDT"}),
    ("/api/mumlar", {"sembol": "BTCUSDT", "periyot": "15m", "adet": "60"}),
    ("/api/izleme", {}),
    ("/api/maliyet-risk", {"sembol": "BTCUSDT", "giris": "100", "hedef": "102",
                           "stop": "99", "butce": "100", "risk": "1.0"}),
    ("/api/plan", {"sembol": "BTCUSDT", "periyot": "1h", "butce": "100",
                   "dilim": "4", "kip": "donemsel", "aralik_gun": "7"}),
    ("/api/gunluk", {}),
    ("/api/saglik", {}),
]


@pytest.mark.parametrize("yol,parametre", UCLAR)
def test_uc_yanit_veriyor(client, yol, parametre):
    yanit = client.get(yol, params=parametre)
    assert yanit.status_code == 200, yanit.text
    assert yanit.json() is not None


def test_sayfa_ve_statik_dosyalar_servis_ediliyor(client):
    sayfa = client.get("/")
    assert sayfa.status_code == 200
    assert "Sadece Öneri" in sayfa.text
    for dosya in ("style.css", "app.js", "grafik.js", "kagit.js"):
        assert client.get(f"/statik/{dosya}").status_code == 200


# --- sayı biçimi ------------------------------------------------------------

def _metin_alanlar(dugum, yol=""):
    """Parasal alan adlarını ve değerlerini toplar."""
    parasal = ("giris", "hedef1", "hedef2", "stop", "basa_bas", "miktar",
               "tutar_usdt", "stop_zarari_usdt", "butce_usdt")
    bulunan = []
    if isinstance(dugum, dict):
        for ad, deger in dugum.items():
            if ad in parasal and deger is not None:
                bulunan.append((f"{yol}.{ad}", deger))
            bulunan.extend(_metin_alanlar(deger, f"{yol}.{ad}"))
    elif isinstance(dugum, list):
        for sira, deger in enumerate(dugum):
            bulunan.extend(_metin_alanlar(deger, f"{yol}[{sira}]"))
    return bulunan


def test_parasal_degerler_metin_olarak_gidiyor(client):
    veri = client.get("/api/oneriler",
                      params={"sembol": "BTCUSDT", "periyot": "15m",
                              "ornek": "true"}).json()
    alanlar = _metin_alanlar(veri)
    assert alanlar, "Kartta parasal alan bulunamadı"
    for yol, deger in alanlar:
        assert isinstance(deger, str), f"{yol} metin değil: {deger!r}"


def test_parasal_degerlerde_bilimsel_gosterim_yok(client):
    """``9.9E-7`` gibi bir fiyatı kullanıcı okuyamaz."""
    ham = client.get("/api/oneriler",
                     params={"sembol": "BTCUSDT", "periyot": "15m",
                             "ornek": "true"}).text
    for yol, deger in _metin_alanlar(json.loads(ham)):
        assert "E" not in deger.upper(), f"{yol}: {deger}"


def test_yuzdeler_sayi_olarak_gidiyor(client):
    """Yüzdeler ölçüm sonucudur, emir fiyatı değil; arayüz onları biçimler."""
    kart = client.get("/api/oneriler",
                      params={"sembol": "BTCUSDT", "periyot": "15m",
                              "ornek": "true"}).json()["kartlar"][0]
    assert isinstance(kart["net_marj_yuzde"], float)
    assert isinstance(kart["brut_marj_yuzde"], float)


# --- öneri motorunun dürüstlüğü ---------------------------------------------

def test_kural_yokken_kart_gonderilmiyor(client):
    veri = client.get("/api/oneriler",
                      params={"sembol": "BTCUSDT", "periyot": "15m"}).json()
    assert veri["durum"] == "kural_yok"
    assert veri["kartlar"] == []
    assert veri["ornek"] is False
    assert veri["aciklama"]["satirlar"]


def test_ornek_kart_ornek_oldugunu_soyluyor(client):
    veri = client.get("/api/oneriler",
                      params={"sembol": "BTCUSDT", "periyot": "15m",
                              "ornek": "true"}).json()
    assert veri["ornek"] is True
    assert "öneri değildir" in veri["ornek_notu"]
    assert len(veri["kartlar"]) == 1


def test_kartta_tarihsel_isabet_ve_orneklem_var(client):
    """SPEC §4.4: kartta "tarihsel isabet oranı (n=...)"."""
    kart = client.get("/api/oneriler",
                      params={"sembol": "BTCUSDT", "periyot": "15m",
                              "ornek": "true"}).json()["kartlar"][0]
    assert 0 <= kart["kanit"]["isabet_orani"] <= 1
    assert kart["kanit"]["olay"] > 0
    assert kart["kanit"]["bagimsiz_olay"] > 0


def test_kutuphanede_adaylar_kabul_edilenlerden_ayri(client):
    """Eşiği geçmemiş adaylar kabul edilmiş kural gibi gösterilmemeli."""
    veri = client.get("/api/kurallar").json()
    assert veri["kabul_edilenler"] == []
    assert len(veri["incelenen_adaylar"]) == 1
    assert "geçmedi" in veri["incelenen_aday_notu"].lower() \
        or "edilmedi" in veri["incelenen_aday_notu"].lower()


def test_kural_deposu_yoksa_anlasilir_hata(bos_client):
    yanit = bos_client.get("/api/oneriler",
                           params={"sembol": "BTCUSDT", "periyot": "15m"})
    assert yanit.status_code == 404
    assert "tarama" in yanit.json()["detail"].lower()


# --- kullanıcı hataları ------------------------------------------------------

def test_ters_stop_400_ve_turkce_mesaj(client):
    yanit = client.get("/api/maliyet-risk",
                       params={"sembol": "BTCUSDT", "giris": "100",
                               "hedef": "102", "stop": "101",
                               "butce": "100", "risk": "1.0"})
    assert yanit.status_code == 400
    assert "Stop" in yanit.json()["detail"]


def test_sayi_olmayan_giris_500_vermiyor(client):
    yanit = client.get("/api/maliyet-risk",
                       params={"sembol": "BTCUSDT", "giris": "yüz",
                               "hedef": "102", "stop": "99",
                               "butce": "100", "risk": "1.0"})
    assert yanit.status_code in (400, 422)


def test_bilinmeyen_periyot_500_vermiyor(client):
    yanit = client.get("/api/mumlar",
                       params={"sembol": "BTCUSDT", "periyot": "7z",
                               "adet": "10"})
    assert yanit.status_code in (400, 404, 422)


# --- mod sınırları ----------------------------------------------------------

def test_mod_sadece_oneri(client):
    veri = client.get("/api/durum").json()
    assert veri["mod"] == MODE == "sadece_oneri"
    assert veri["mod_tr"] == MODE_TR


def test_saglik_uca_gore_emir_yetkisi_yok(client):
    veri = client.get("/api/saglik").json()
    assert "yok" in veri["emir_yetkisi"]
    assert "kullanılmıyor" in veri["api_anahtari"]


def test_emir_uclari_yalnizca_kagit_ve_demo_altinda(client):
    """Emirle ilgili her uç ``/api/kagit/`` (kâğıt defter) ya da ``/api/demo/``
    (Binance Demo Mode, sahte para) altında. Canlı hesaba emir ucu yok."""
    app = client.app
    yollar = {route.path for route in app.routes}
    yasak = ("order", "emir", "trade", "islem-ac", "satin-al")
    assert not [
        y for y in yollar
        if any(k in y.lower() for k in yasak)
        and not y.startswith(("/api/kagit/", "/api/demo/"))
    ]


def test_yazma_metodu_yalnizca_kagit_ve_demo_altinda(client):
    """Faz 3 uçları hiçbir şeyi değiştirmiyor; POST yalnızca kâğıt işlemde ve Demo'da."""
    from fastapi.routing import APIRoute

    for route in client.app.routes:
        if isinstance(route, APIRoute) and not route.path.startswith(
                ("/api/kagit/", "/api/demo/")):
            assert route.methods <= {"GET", "HEAD"}, route.path


def test_sunucu_yalnizca_yerel_adrese_baglanir():
    """SPEC §5. Bayrakla değiştirilebilir olmamalı."""
    assert HOST == "127.0.0.1"
    import albsat.cli.serve as serve

    secenekler = serve.build_parser()._option_string_actions
    assert not any("host" in ad or "adres" in ad for ad in secenekler)


def test_uyari_metni_her_yanitta_ulasilabilir(client):
    veri = client.get("/api/durum").json()
    assert "yatırım tavsiyesi değildir" in veri["uyari"]


# --- grafik ----------------------------------------------------------------

def test_grafik_yuksekligi_canvas_ozniteliginden_geri_okunmuyor():
    """Retina ekranda grafik her "Yenile"de ikiye katlanıyordu (22 Eylül 2026).

    Sebep: ``grafik.js`` yüksekliği canvas'ın ``height`` özniteliğinden
    okuyor, sonra oraya piksel oranıyla çarpılmış değeri yazıyordu; bir
    sonraki çizim büyümüş değeri okuyordu (260 → 520 → 1040 ...). Piksel
    oranı 1 olan ekranda görünmüyordu. Tarayıcı testi bu depoda Node
    gerektirmesin diye, hatanın mekanizması burada metin olarak korunuyor.
    """
    from albsat.api.app import STATIC_DIR

    grafik = (STATIC_DIR / "grafik.js").read_text(encoding="utf-8")
    sayfa = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'getAttribute("height")' not in grafik
    assert "getAttribute('height')" not in grafik
    assert "canvas.height =" in grafik           # tampon hâlâ ayarlanıyor
    assert "dataset.yukseklik" in grafik         # yükseklik buradan okunuyor

    import re
    canvas = re.search(r"<canvas[^>]*id=\"grafik\"[^>]*>", sayfa).group(0)
    assert " height=" not in canvas
    assert "data-yukseklik" in canvas


# --- eski kodun tarayıcıda kalmaması ----------------------------------------
#
# 22 Eylül 2026: grafik hatası düzeltilip gönderildi ama kullanıcının
# ekranında sürdü. Önceden açılmış sekme eski grafik.js'i çalıştırıyordu ve
# sunucu tarayıcıya hiçbir önbellek talimatı vermiyordu.

def test_hicbir_yanit_tarayicida_saklanmiyor(client):
    for yol in ("/", "/statik/grafik.js", "/statik/app.js", "/statik/kagit.js",
                "/statik/style.css", "/api/durum"):
        yanit = client.get(yol)
        assert yanit.headers.get("cache-control") == "no-store", yol


def test_sayfa_surumlu_adreslerle_geliyor(client):
    from albsat.api.app import asset_version

    surum = asset_version()
    sayfa = client.get("/").text
    assert "{{SURUM}}" not in sayfa
    assert f'<meta name="arayuz-surumu" content="{surum}">' in sayfa
    for dosya in ("style.css", "grafik.js", "app.js", "kagit.js"):
        assert f"/statik/{dosya}?v={surum}" in sayfa


def test_api_yanitlari_surumu_tasiyor(client):
    from albsat.api.app import VERSION_HEADER, asset_version

    yanit = client.get("/api/durum")
    assert yanit.headers.get(VERSION_HEADER) == asset_version()


def test_surum_dosya_degisince_degisir_ayni_icerikte_ayni_kalir(tmp_path):
    """İçerikten türetilir: aynı dosyalar her makinede aynı kimliği verir."""
    import shutil

    from albsat.api.app import STATIC_DIR, asset_version

    bir = tmp_path / "bir"
    iki = tmp_path / "iki"
    shutil.copytree(STATIC_DIR, bir)
    shutil.copytree(STATIC_DIR, iki)
    assert asset_version(bir) == asset_version(iki)

    once = asset_version(bir)
    grafik = bir / "grafik.js"
    grafik.write_text(grafik.read_text(encoding="utf-8") + "\n// değişti\n",
                      encoding="utf-8")
    assert asset_version(bir) != once
