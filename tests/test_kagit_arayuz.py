"""Kâğıt işlem uçlarının ve yerel korumanın testleri (Faz 4).

Üç şeyi koruyorlar:

1. **Yerel koruma.** Arayüz yalnızca ``127.0.0.1``/``localhost`` adıyla
   açılır; durum değiştiren istek yalnızca arayüzün kendi sayfasından gelir.
   Tarayıcıda açık başka bir site, kullanıcının tarayıcısı üzerinden kâğıt
   hesabı değiştiremez.
2. **Risk kapıları arayüzde de geçerli.** Elle emir, kural sinyaliyle aynı
   kapılardan geçer; ön izleme hiçbir şey kaydetmez.
3. **Binance'e dokunan kod yok.** Arayüz katmanı imzalı istek, anahtar ya da
   Anahtar Zinciri koduna erişmez.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_kagit_islem import COSTS, RULES, market

from albsat.api.app import STATIC_DIR, AppState, create_app
from albsat.api.runtime import Runtime
from albsat.core.clock import utc_now
from albsat.data.live import Quote
from albsat.modes.state import MODE_ADVICE, MODE_PAPER
from albsat.notify.base import MemoryNotifier
from albsat.paper.engine import PaperEngine
from albsat.paper.ledger import STATUS_CANCELLED, STATUS_PENDING

D = Decimal
BASE = "http://127.0.0.1"
YAZ = {"X-Albsat-Istek": "1"}


@dataclass
class StubMarket:
    """Canlı piyasa yerine: sağlıklı, sabit bir defter."""

    alis: Decimal = D("60050")
    satis: Decimal = D("60050.01")

    def quote(self, sembol):
        return Quote(self.alis, self.satis, utc_now())

    def bid(self, sembol):
        return self.alis

    def last_price(self, sembol):
        return self.alis

    def usdttry(self):
        return D("41.20")

    def spread_samples(self, sembol):
        return 300

    def market_state(self, sembol, periyot, rules):
        now = utc_now()
        return market(now, son_veri_utc=now - timedelta(seconds=5), kurallar=rules)


class StubRunner:
    def __init__(self, market: StubMarket) -> None:
        self.market = market

    def marks(self):
        return {"BTCUSDT": self.market.alis}

    def status(self):
        return {"akis_bagli": True, "yedek_yoklama": False, "coinler": []}


def _runtime(tmp_path, *, live: bool = True) -> Runtime:
    notifier = MemoryNotifier()
    engine = PaperEngine(
        tmp_path, symbols=("BTCUSDT",), notifier=notifier,
        costs_for=lambda symbol: COSTS, rules_for=lambda symbol: RULES,
    )
    engine.modes.start_session()
    stub = StubMarket()
    return Runtime(
        root=tmp_path, symbols=("BTCUSDT",), periods=("15m", "1h"), notifier=notifier,
        engine=engine, market=stub,  # type: ignore[arg-type]
        runner=StubRunner(stub) if live else None,  # type: ignore[arg-type]
        telegram_note="Telegram bu testte kapalı.",
    )


@pytest.fixture
def runtime(tmp_path):
    return _runtime(tmp_path)


@pytest.fixture
def client(tmp_path, runtime):
    state = AppState(veri_dizini=tmp_path, semboller=("BTCUSDT",),
                     periyotlar=("15m", "1h"), runtime=runtime)
    return TestClient(create_app(state), base_url=BASE)


def _post(client, path, body=None, **headers):
    return client.post(path, json=body or {}, headers={**YAZ, **headers})


def _kagit(client):
    assert _post(client, "/api/kagit/mod", {"sembol": "BTCUSDT", "mod": MODE_PAPER}).status_code \
        == 200


EMIR = {"sembol": "BTCUSDT", "periyot": "15m", "giris": "60000", "hedef": "60900",
        "stop": "59400"}


# --- yerel koruma ------------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8756", "192.168.1.5:8756",
                                  "127.0.0.1.evil.example"])
def test_yabanci_host_reddedilir(tmp_path, runtime, host):
    """Başka bir alan adı bu adrese yönlendirilse bile (DNS rebinding) veri okunamaz."""
    state = AppState(veri_dizini=tmp_path, runtime=runtime)
    yanit = TestClient(create_app(state), base_url=f"http://{host}").get("/api/kagit/durum")
    assert yanit.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8756", "localhost:8756", "localhost"])
def test_yerel_host_kabul_edilir(tmp_path, runtime, host):
    state = AppState(veri_dizini=tmp_path, runtime=runtime)
    yanit = TestClient(create_app(state), base_url=f"http://{host}").get("/api/kagit/durum")
    assert yanit.status_code == 200


def test_ozel_baslik_olmadan_yazilamaz(client):
    yanit = client.post("/api/kagit/acil-durdur", json={})
    assert yanit.status_code == 403
    assert "başlık" in yanit.json()["detail"]


def test_json_olmayan_govde_reddedilir(client):
    yanit = client.post("/api/kagit/acil-durdur", content="pozisyonlari_kapat=true",
                        headers={**YAZ, "Content-Type": "application/x-www-form-urlencoded"})
    assert yanit.status_code == 403


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "https://127.0.0.1.evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_baska_siteden_gelen_yazma_reddedilir(client, runtime, headers):
    _kagit(client)
    yanit = _post(client, "/api/kagit/acil-durdur", {}, **headers)
    assert yanit.status_code == 403
    assert runtime.engine.modes.get("BTCUSDT") == MODE_PAPER  # hiçbir şey değişmedi


def test_kendi_sayfasindan_gelen_yazma_kabul(client):
    yanit = _post(client, "/api/kagit/acil-durdur", {},
                  Origin="http://127.0.0.1:8756", **{"Sec-Fetch-Site": "same-origin"})
    assert yanit.status_code == 200


def test_bilinmeyen_alan_reddedilir(client):
    yanit = _post(client, "/api/kagit/acil-durdur", {"hepsini_sat": True})
    assert yanit.status_code == 422


# --- durum ------------------------------------------------------------------------


def test_durum_acilista_sadece_oneri_ve_metin_sayilar(client):
    veri = client.get("/api/kagit/durum").json()
    assert [item["mod"] for item in veri["modlar"]] == [MODE_ADVICE]
    assert veri["hesap"]["baslangic_usdt"] == "100.00"
    assert isinstance(veri["hesap"]["serbest_usdt"], str)
    assert veri["aktif"] == []
    assert {item["mod"] for item in veri["kilitli_modlar"]} == {
        "demo", "yari_otomatik", "tam_otomatik"}
    assert veri["ozet"]["kural"]["islem"] == 0 and veri["ozet"]["elle"]["islem"] == 0
    assert veri["maliyet"]["BTCUSDT"] == COSTS.kaynak_tr
    adlar = {item["ad"] for item in veri["risk"]["gostergeler"]}
    assert {"gunluk_zarar", "art_arda_kayip", "acik_pozisyon"} <= adlar


def test_kagit_motoru_yoksa_503(tmp_path):
    state = AppState(veri_dizini=tmp_path)
    yanit = TestClient(create_app(state), base_url=BASE).get("/api/kagit/durum")
    assert yanit.status_code == 503


# --- mod ----------------------------------------------------------------------------


def test_mod_degisimi_denetime_ve_bildirime_yazilir(client, runtime):
    _kagit(client)
    assert runtime.engine.modes.get("BTCUSDT") == MODE_PAPER
    kayit = runtime.engine.audit.recent(5)[0]
    assert kayit.tur == "mod" and kayit.kaynak == "arayuz"
    assert any("MOD" in text for text in runtime.notifier.texts)


@pytest.mark.parametrize("mod", ["demo", "yari_otomatik", "tam_otomatik"])
def test_kilitli_mod_secilemez(client, runtime, mod):
    yanit = _post(client, "/api/kagit/mod", {"sembol": "BTCUSDT", "mod": mod})
    assert yanit.status_code == 400
    assert "kilitli" in yanit.json()["detail"]
    assert runtime.engine.modes.get("BTCUSDT") == MODE_ADVICE


def test_cevrimdisiyken_kagit_mod_acilmaz(tmp_path):
    runtime = _runtime(tmp_path, live=False)
    state = AppState(veri_dizini=tmp_path, runtime=runtime)
    client = TestClient(create_app(state), base_url=BASE)
    yanit = _post(client, "/api/kagit/mod", {"sembol": "BTCUSDT", "mod": MODE_PAPER})
    assert yanit.status_code == 409
    assert runtime.engine.modes.get("BTCUSDT") == MODE_ADVICE


def test_izlenmeyen_coin_reddedilir(client):
    yanit = _post(client, "/api/kagit/mod", {"sembol": "ETHUSDT", "mod": MODE_PAPER})
    assert yanit.status_code == 400


# --- limitler ------------------------------------------------------------------------


def test_limit_degisimi_kaydedilir_ve_denetlenir(client, runtime):
    yanit = _post(client, "/api/kagit/limitler",
                  {"degerler": {"islem_basi_risk_yuzde": "0,5", "art_arda_kayip_limiti": "3"}})
    assert yanit.status_code == 200, yanit.text
    limits = runtime.engine.limits
    assert limits.islem_basi_risk_yuzde == D("0.5")
    assert limits.art_arda_kayip_limiti == 3
    kayit = runtime.engine.audit.recent(1)[0]
    assert kayit.tur == "limit" and "İşlem başına risk" in kayit.ozet
    assert any("RİSK LİMİTİ" in text for text in runtime.notifier.texts)


@pytest.mark.parametrize("degerler", [
    {"islem_basi_risk_yuzde": "5"},          # üst sınır 2
    {"gunluk_max_zarar_yuzde": "abc"},
    {"art_arda_kayip_limiti": "2.5"},        # tam sayı olmalı
    {"bilinmeyen": "1"},
])
def test_gecersiz_limit_reddedilir_ve_hicbir_sey_degismez(client, runtime, degerler):
    once = runtime.engine.limits
    yanit = _post(client, "/api/kagit/limitler", {"degerler": degerler})
    assert yanit.status_code == 400
    assert runtime.engine.limits == once


def test_oneri_karti_butceyi_risk_limitlerinden_okur(client):
    _post(client, "/api/kagit/limitler", {"degerler": {"butce_usdt": "250"}})
    assert client.get("/api/durum").json()["butce_usdt"] == "250"


# --- elle emir ------------------------------------------------------------------------


def test_on_izleme_hicbir_sey_kaydetmez(client, runtime):
    _kagit(client)
    yanit = client.get("/api/kagit/on-izleme", params=EMIR)
    assert yanit.status_code == 200, yanit.text
    karar = yanit.json()["karar"]
    assert karar["izin"] is True
    assert karar["pozisyon"]["stop_zarari_usdt"]
    assert runtime.engine.ledger.recent(10) == ()


def test_sadece_oneri_modunda_elle_emir_acilmaz(client, runtime):
    yanit = _post(client, "/api/kagit/emir", EMIR)
    assert yanit.status_code == 200
    veri = yanit.json()
    assert veri["emir"] is None
    kapali = [item["ad"] for item in veri["karar"]["kapilar"] if not item["gecti"]]
    assert kapali == ["mod"]
    assert runtime.engine.ledger.recent(10) == ()


def test_elle_emir_acilir_ve_ayri_sayilir(client, runtime):
    _kagit(client)
    veri = _post(client, "/api/kagit/emir", {**EMIR, "azami_tutma_mum": 2}).json()
    emir = veri["emir"]
    assert emir is not None, veri["karar"]
    assert emir["kaynak"] == "elle" and emir["durum"] == STATUS_PENDING
    assert D(emir["stop_zarari_usdt"]) <= D("1")  # %1 risk, 100 USDT
    kayit = runtime.engine.ledger.get(emir["id"])
    assert kayit.azami_tutma_ms == 2 * 15 * 60_000
    aktif = client.get("/api/kagit/durum").json()["aktif"]
    assert [item["id"] for item in aktif] == [emir["id"]]
    denetim = client.get("/api/denetim").json()["kayitlar"]
    assert any(item["tur"] == "emir" and item["kaynak"] == "arayuz" for item in denetim)


def test_elle_fiyat_borsa_adimina_yuvarlanir(client):
    _kagit(client)
    veri = client.get("/api/kagit/on-izleme",
                      params={**EMIR, "giris": "60000.019", "hedef": "60900.001"}).json()
    assert veri["fiyatlar"]["giris"] == "60000.01"   # alış aşağı
    assert veri["fiyatlar"]["hedef"] == "60900.01"   # satış yukarı
    assert len(veri["yuvarlama"]) == 2


def test_en_iyi_satisa_esit_giris_limit_maker_kapisinda_kalir(client, runtime):
    _kagit(client)
    veri = _post(client, "/api/kagit/emir", {**EMIR, "giris": "60050.01"}).json()
    assert veri["emir"] is None
    kapali = [item["ad"] for item in veri["karar"]["kapilar"] if not item["gecti"]]
    assert kapali == ["limit_maker"]


def test_ters_fiyat_kapida_kalir(client):
    _kagit(client)
    veri = _post(client, "/api/kagit/emir", {**EMIR, "stop": "60500"}).json()
    assert veri["emir"] is None
    assert "fiyatlar" in [item["ad"] for item in veri["karar"]["kapilar"] if not item["gecti"]]


def test_sayi_olmayan_fiyat_400(client):
    _kagit(client)
    yanit = _post(client, "/api/kagit/emir", {**EMIR, "giris": "altmış bin"})
    assert yanit.status_code == 400
    assert "sayı" in yanit.json()["detail"]


def test_cevrimdisiyken_elle_emir_acilmaz(tmp_path):
    runtime = _runtime(tmp_path, live=False)
    runtime.engine.modes.set("BTCUSDT", MODE_PAPER)
    state = AppState(veri_dizini=tmp_path, runtime=runtime)
    yanit = _post(TestClient(create_app(state), base_url=BASE), "/api/kagit/emir", EMIR)
    assert yanit.status_code == 409


# --- iptal, acil durdur, sıfırlama -------------------------------------------------------


def test_iptal_ve_ikinci_iptal(client, runtime):
    _kagit(client)
    emir = _post(client, "/api/kagit/emir", EMIR).json()["emir"]
    yanit = _post(client, "/api/kagit/iptal", {"id": emir["id"]})
    assert yanit.status_code == 200
    assert runtime.engine.ledger.get(emir["id"]).durum == STATUS_CANCELLED
    assert _post(client, "/api/kagit/iptal", {"id": emir["id"]}).status_code == 409


def test_acil_durdur_modlari_indirir_bekleyenleri_iptal_eder(client, runtime):
    _kagit(client)
    emir = _post(client, "/api/kagit/emir", EMIR).json()["emir"]
    veri = _post(client, "/api/kagit/acil-durdur", {"pozisyonlari_kapat": True}).json()
    assert veri["iptal_edilen"] == 1
    assert [item["mod"] for item in veri["modlar"]] == [MODE_ADVICE]
    assert runtime.engine.ledger.get(emir["id"]).durum == STATUS_CANCELLED
    assert any("ACİL DURDUR" in text for text in runtime.notifier.texts)


def test_hesap_sifirlama_onay_ister_ve_acik_emirle_yapilmaz(client, runtime):
    _kagit(client)
    emir = _post(client, "/api/kagit/emir", EMIR).json()["emir"]
    assert _post(client, "/api/kagit/hesap-sifirla", {"onay": "evet"}).status_code == 400
    assert _post(client, "/api/kagit/hesap-sifirla", {"onay": "SIFIRLA"}).status_code == 409
    _post(client, "/api/kagit/iptal", {"id": emir["id"]})
    yanit = _post(client, "/api/kagit/hesap-sifirla", {"onay": "sıfırla"})  # küçük harf de olur
    assert yanit.status_code == 200, yanit.text
    assert client.get("/api/kagit/durum").json()["hesap"]["donem_id"] == yanit.json()["donem_id"]


def test_durdurulmamis_kural_acilamaz(client):
    assert _post(client, "/api/kagit/kural-ac", {"kural": "yok"}).status_code == 409


# --- okuma uçları -----------------------------------------------------------------------


def test_csv_indirilir(client):
    yanit = client.get("/api/kagit/islemler.csv")
    assert yanit.status_code == 200
    assert yanit.headers["content-type"].startswith("text/csv")
    assert "attachment" in yanit.headers["content-disposition"]


def test_piyasa_ve_bildirimler(client):
    piyasa = client.get("/api/kagit/piyasa", params={"periyot": "1h"}).json()
    assert piyasa["canli"] is True
    coin = piyasa["coinler"][0]
    assert coin["alis"] == "60050" and coin["periyot"] == "1h"
    assert {item["ad"] for item in coin["kapilar"]} >= {"bayat_veri", "spread"}
    bildirim = client.get("/api/bildirimler").json()
    assert bildirim["telegram"]["kurulu"] is False


def test_islemler_listesi(client):
    _kagit(client)
    _post(client, "/api/kagit/emir", EMIR)
    veri = client.get("/api/kagit/islemler").json()
    assert len(veri["islemler"]) == 1
    assert veri["islemler"][0]["kaynak_tr"] == "Elle"


# --- gerçek kurulum (ağsız) ----------------------------------------------------------------


def test_runtime_cevrimdisi_kurulur_ve_acilis_denetlenir(tmp_path):
    runtime = Runtime.build(tmp_path, symbols=("BTCUSDT", "SOLUSDT"), periods=("15m", "1h"),
                            online=False, use_telegram=False)
    assert runtime.runner is None
    state = AppState(veri_dizini=tmp_path, runtime=runtime)
    client = TestClient(create_app(state), base_url=BASE)
    veri = client.get("/api/kagit/durum").json()
    assert veri["canli"] is False
    assert {item["mod"] for item in veri["modlar"]} == {MODE_ADVICE}
    assert runtime.engine.audit.recent(1)[0].tur == "acilis"
    assert "varsayım" in veri["maliyet"]["BTCUSDT"]


def test_acilista_kagit_mod_hatirlanmaz(tmp_path):
    """SPEC §2: her açılışta Sadece Öneri. Önceki mod yalnızca bilgi olarak kalır."""
    first = Runtime.build(tmp_path, symbols=("BTCUSDT",), periods=("15m",), online=False,
                          use_telegram=False)
    first.engine.modes.set("BTCUSDT", MODE_PAPER)
    second = Runtime.build(tmp_path, symbols=("BTCUSDT",), periods=("15m",), online=False,
                           use_telegram=False)
    assert second.engine.modes.get("BTCUSDT") == MODE_ADVICE
    assert second.previous_modes["BTCUSDT"] == MODE_PAPER


# --- Binance'e dokunan kod yok -------------------------------------------------------------


def test_arayuz_katmani_imzali_istek_ve_anahtar_koduna_erismez():
    api_dir = STATIC_DIR.parent
    # Telegram jetonu Anahtar Zinciri'nden okunur (runtime.py); Binance anahtarı asla.
    yasak = ("exchange.signed", "exchange.keys", "albsat-binance", "X-MBX-APIKEY",
             "SignedReader", "load_key", "/api/v3/order")
    for path in sorted(api_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for kelime in yasak:
            assert kelime not in text, f"{path.name}: {kelime}"
