"""Kâğıt işlem motoru testleri (SPEC.md §10 Faz 4: "paper sonuçları kaydediliyor").

Mumlar elle kurulur; her test tek bir dolum kuralını ya da bir durum
geçişini sınar. Saatler parametre olarak verildiği için gerçek zamanı
beklemek gerekmez.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_sizing import PAYLOAD

from albsat.core.audit import SOURCE_UI
from albsat.core.clock import to_ms
from albsat.core.fees import flat_table
from albsat.core.filters import SymbolRules
from albsat.modes.state import MODE_ADVICE, MODE_PAPER
from albsat.notify.base import MemoryNotifier
from albsat.paper import fills
from albsat.paper.engine import OrderMeta, PaperEngine
from albsat.paper.fills import Candle, PaperCosts
from albsat.paper.ledger import (
    CLIENT_ID_PREFIX,
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_PENDING,
)
from albsat.risk.engine import SOURCE_MANUAL, SOURCE_RULE, OrderIntent
from albsat.risk.limits import RiskLimits
from albsat.risk.market import MarketState

D = Decimal
# Pazartesi 21 Eylül 2026, 12:00:30 UTC (15:00:30 İstanbul).
NOW = datetime(2026, 9, 21, 12, 0, 30, tzinfo=UTC)
BASE_MS = to_ms(datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
MINUTE = 60_000
RULES = SymbolRules.from_exchange_info(PAYLOAD)
SOL_RULES = SymbolRules.from_exchange_info(
    {**PAYLOAD, "symbol": "SOLUSDT", "baseAsset": "SOL", "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01000000",
         "maxPrice": "10000.00000000", "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "minQty": "0.00100000",
         "maxQty": "90000.00000000", "stepSize": "0.00100000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000",
         "applyMinToMarket": True, "maxNotional": "9000000.00000000",
         "applyMaxToMarket": False, "avgPriceMins": 5},
    ]}
)
COSTS = PaperCosts(flat_table("*", "0.001", "0.001"), D("0.02"), "test: %0,1 düz")


def candle(minute: int, o: str, h: str, low: str, c: str) -> Candle:
    start = BASE_MS + minute * MINUTE
    return Candle(start, start + MINUTE, D(o), D(h), D(low), D(c))


def quiet(minute: int, price: str = "60100") -> Candle:
    """Hiçbir emre dokunmayan sakin bir mum."""
    return candle(minute, price, price, price, price)


def market(now: datetime = NOW, **changes) -> MarketState:
    values = dict(
        sembol="BTCUSDT",
        son_veri_utc=now - timedelta(seconds=20),
        son_fiyat=D("60050"),
        atr_yuzde=D("0.5"),
        atr_medyan_yuzde=D("0.4"),
        spread_yuzde=D("0.0002"),
        spread_medyan_yuzde=D("0.0002"),
        hacim_24s_usdt=D("900000000"),
        btc_60dk_degisim_yuzde=D("0.4"),
        kurallar=RULES,
    )
    values.update(changes)
    return MarketState(**values)


def intent(**changes) -> OrderIntent:
    values = dict(
        sembol="BTCUSDT", periyot="15m",
        giris=D("60000"), hedef=D("60900"), stop=D("59400"),
        kaynak=SOURCE_RULE, kural_kimligi="kural-1",
    )
    values.update(changes)
    return OrderIntent(**values)


@pytest.fixture
def notifier() -> MemoryNotifier:
    return MemoryNotifier()


@pytest.fixture
def paper(tmp_path, notifier) -> PaperEngine:
    engine = PaperEngine(
        tmp_path,
        symbols=("BTCUSDT", "SOLUSDT"),
        notifier=notifier,
        costs_for=lambda symbol: COSTS,
        rules_for=lambda symbol: {"BTCUSDT": RULES, "SOLUSDT": SOL_RULES}.get(symbol),
    )
    engine.modes.start_session()
    engine.modes.set("BTCUSDT", MODE_PAPER)
    return engine


def place(paper: PaperEngine, now: datetime = NOW, meta: OrderMeta | None = None, **changes):
    decision, order = paper.place(intent(**changes), market=market(now), now=now, meta=meta)
    assert order is not None, decision.ozet_tr
    return order


def relax(paper: PaperEngine, **changes) -> None:
    values = RiskLimits().to_json()
    values.update(changes)
    paper.limit_store.write(RiskLimits.from_json(values))


# --- emir açma -----------------------------------------------------------------


def test_emir_risk_kapilarindan_gecince_kaydedilir(paper, notifier):
    order = place(paper)
    assert order.durum == STATUS_PENDING
    assert order.istemci_kimligi.startswith(CLIENT_ID_PREFIX)
    assert len(order.istemci_kimligi) <= 36
    # Emir verildiği dakikadan sonraki ilk mumdan geçerli.
    assert order.aktif_ms == BASE_MS + MINUTE
    # 15m periyot, 1 mum geçerlilik.
    assert order.gecerlilik_bitis_ms == BASE_MS + MINUTE + 15 * MINUTE
    assert order.azami_tutma_ms == 4 * 15 * MINUTE
    # İşlem başına risk %1 → stopta zarar 1 USDT'yi aşmaz.
    assert D(order.stop_zarari_usdt) <= D("1")
    assert any("KÂĞIT EMİR" in text for text in notifier.texts)
    assert paper.audit.recent(5)[0].tur == "emir"


def test_kagit_modunda_olmayan_coine_emir_acilmaz(paper):
    paper.modes.set("BTCUSDT", MODE_ADVICE)
    decision, order = paper.place(intent(), market=market(), now=NOW)
    assert order is None
    assert not decision.izin
    assert "mod" in [gate.ad for gate in decision.kapali_kapilar]
    assert paper.ledger.active() == ()
    assert paper.audit.recent(1)[0].tur == "emir_reddedildi"


def test_es_zamanli_pozisyon_siniri_bekleyen_emri_de_sayar(paper):
    place(paper)
    decision, order = paper.place(intent(), market=market(), now=NOW)
    assert order is None
    assert "es_zamanli" in [gate.ad for gate in decision.kapali_kapilar]


def test_bekleyen_emir_serbest_bakiyeyi_kilitler(paper):
    order = place(paper)
    view = paper.account()
    assert view.nakit_usdt == D("100")
    assert view.kilitli_usdt == D(order.tutar_usdt)
    assert view.serbest_usdt == D("100") - D(order.tutar_usdt)


# --- giriş dolumu ------------------------------------------------------------------


def test_emrin_verildigi_dakikanin_mumu_dolum_saymaz(paper):
    order = place(paper)
    events = paper.process_candle("BTCUSDT", candle(0, "60100", "60100", "59900", "60000"))
    assert events.dolan == []
    assert paper.ledger.get(order.id).durum == STATUS_PENDING


def test_fiyata_dokunmak_yetmez_icinden_gecmeli(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60100", "60100", "60000", "60050"))
    assert paper.ledger.get(order.id).durum == STATUS_PENDING
    events = paper.process_candle("BTCUSDT", candle(2, "60050", "60060", "59999.99", "60010"))
    assert [item.id for item in events.dolan] == [order.id]
    filled = paper.ledger.get(order.id)
    assert filled.durum == STATUS_OPEN
    assert filled.dolum_ms == BASE_MS + 3 * MINUTE


def test_komisyon_coinden_duser_ve_toz_hesapta_kalir(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    filled = paper.ledger.get(order.id)
    quantity = D(order.miktar)
    fee = quantity * D("0.001")
    assert D(filled.komisyon_coin) == fee
    assert D(filled.alinan) == quantity - fee
    # Satılabilir miktar stepSize'a (0.00001) aşağı yuvarlanır.
    assert D(filled.satilacak) == RULES.round_quantity(quantity - fee)
    assert D(filled.satilacak) < D(filled.alinan)
    assert D(filled.toz) == D(filled.alinan) - D(filled.satilacak)
    # Toz, pozisyon kapandıktan sonra hesapta coin olarak kalır.
    paper.process_candle("BTCUSDT", candle(2, "60500", "60950", "60400", "60900"))
    view = paper.account()
    assert view.coinler == {"BTCUSDT": D(filled.toz)}


def test_onceki_islemin_tozu_sonraki_satisa_eklenir(paper):
    """Gerçek hesapta satış serbest bakiyeden verilir: toz birikmez."""
    relax(paper, kayip_sonrasi_soguma_mum=0)
    first = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    paper.process_candle("BTCUSDT", candle(2, "60500", "60950", "60400", "60900"))
    first = paper.ledger.get(first.id)
    assert D(first.onceki_toz) == 0
    carried = D(first.toz)
    assert carried > 0
    assert paper.dust("BTCUSDT") == carried

    later = NOW + timedelta(minutes=3)
    second = place(paper, now=later)
    paper.process_candle("BTCUSDT", candle(4, "60050", "60050", "59990", "60000"))
    second = paper.ledger.get(second.id)
    assert D(second.onceki_toz) == carried
    assert D(second.satilacak) == RULES.round_quantity(D(second.alinan) + carried)
    # Kalan toz hiçbir zaman bir stepSize'ı aşmaz.
    assert D(second.toz) < D("0.00001")
    # Açık pozisyonun satılacağı ayrıldığı için serbest toz yalnızca kalan küsurat.
    assert paper.dust("BTCUSDT") == D(second.toz)


def test_ayni_mum_iki_kez_islenmez(paper):
    order = place(paper)
    bar = candle(1, "60050", "60050", "59990", "60000")
    paper.process_candle("BTCUSDT", bar)
    paper.process_candle("BTCUSDT", candle(2, "60000", "60000", "59300", "59350"))
    closed = paper.ledger.get(order.id)
    assert closed.durum == STATUS_CLOSED
    # Eski mum yeniden gelirse hiçbir şey olmaz.
    events = paper.process_candle("BTCUSDT", bar)
    assert events.dolan == [] and events.kapanan == []


# --- çıkışlar ------------------------------------------------------------------------


def test_hedef_asilinca_maker_komisyonla_kapanir(paper, notifier):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    # Hedefe eşit en yüksek yetmez.
    paper.process_candle("BTCUSDT", candle(2, "60500", "60900", "60400", "60800"))
    assert paper.ledger.get(order.id).durum == STATUS_OPEN
    events = paper.process_candle("BTCUSDT", candle(3, "60800", "60900.01", "60700", "60850"))
    closed = events.kapanan[0]
    assert closed.cikis_sebebi == fills.EXIT_TARGET
    assert D(closed.cikis_fiyati) == D("60900")
    sold = D(closed.satilacak)
    gross = D("60900") * sold
    assert D(closed.gelir_usdt) == gross - gross * D("0.001")
    # Net: satış geliri − alış tutarı + hesapta kalan tozun çıkış fiyatından değeri.
    dust = D(closed.toz) - D(closed.onceki_toz)
    assert D(closed.net_usdt) == D(closed.gelir_usdt) + dust * D("60900") - D(order.tutar_usdt)
    assert D(closed.net_usdt) > 0
    assert any("HEDEFE ULAŞTI" in text for text in notifier.texts)
    # Nakit: başlangıç − alış + satış geliri.
    view = paper.account()
    assert view.nakit_usdt == D("100") - D(order.tutar_usdt) + D(closed.gelir_usdt)
    assert view.kilitli_usdt == 0


def test_stop_kaymayla_ve_taker_komisyonla_kapanir(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    events = paper.process_candle("BTCUSDT", candle(2, "59800", "59800", "59350", "59380"))
    closed = events.kapanan[0]
    assert closed.cikis_sebebi == fills.EXIT_STOP
    # min(stop, açılış) = 59400; %0,02 kayma → 59388.12
    assert D(closed.cikis_fiyati) == D("59388.12")
    assert D(closed.net_usdt) < 0
    # Gerçekleşen zarar, emir açılırken hesaplanan stop zararını aşmaz.
    assert -D(closed.net_usdt) <= D(order.stop_zarari_usdt) + D("0.000001")


def test_bosluklu_acilista_stop_acilistan_doler(paper):
    place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    events = paper.process_candle("BTCUSDT", candle(2, "59000", "59100", "58900", "59050"))
    closed = events.kapanan[0]
    # Açılış 59000 stopun altında: 59000 × 0,9998 = 58988.2
    assert D(closed.cikis_fiyati) == D("58988.20")


def test_ayni_mumda_stop_ve_hedef_gorulurse_stop_sayilir(paper):
    place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    events = paper.process_candle("BTCUSDT", candle(2, "60000", "61000", "59000", "60500"))
    assert events.kapanan[0].cikis_sebebi == fills.EXIT_STOP


def test_dolum_mumunda_stop_gorulurse_ayni_mumda_kapanir(paper):
    order = place(paper)
    events = paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59300", "59500"))
    assert [item.id for item in events.dolan] == [order.id]
    assert events.kapanan[0].cikis_sebebi == fills.EXIT_STOP


def test_dolum_mumunda_hedef_sayilmaz(paper):
    order = place(paper)
    events = paper.process_candle("BTCUSDT", candle(1, "60050", "61000", "59990", "60950"))
    assert events.kapanan == []
    assert paper.ledger.get(order.id).durum == STATUS_OPEN


def test_tutma_suresi_dolunca_piyasa_fiyatindan_kapanir(paper):
    order = place(paper, meta=OrderMeta(azami_tutma_mum=1))
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    filled = paper.ledger.get(order.id)
    deadline = filled.dolum_ms + 15 * MINUTE
    minute = 2
    while BASE_MS + (minute + 1) * MINUTE < deadline:
        events = paper.process_candle("BTCUSDT", quiet(minute))
        assert events.kapanan == []
        minute += 1
    events = paper.process_candle("BTCUSDT", quiet(minute, "60200"))
    closed = events.kapanan[0]
    assert closed.cikis_sebebi == fills.EXIT_TIME
    # 60200 × 0,9998 = 60187.96, taker komisyon.
    assert D(closed.cikis_fiyati) == D("60187.96")
    gross = D("60187.96") * D(closed.satilacak)
    assert D(closed.gelir_usdt) == gross - gross * D("0.001")


def test_gecerlilik_dolunca_giris_iptal_edilir(paper):
    order = place(paper)
    # 12:01'den 12:16'ya kadar (bir 15m mum) geçerli; son dakikada hâlâ dolabilir.
    for minute in range(1, 15):
        paper.process_candle("BTCUSDT", quiet(minute))
    assert paper.ledger.get(order.id).durum == STATUS_PENDING
    events = paper.process_candle("BTCUSDT", quiet(15))
    assert [item.id for item in events.iptal] == [order.id]
    assert paper.ledger.get(order.id).durum == STATUS_CANCELLED
    assert paper.account().kilitli_usdt == 0


# --- uygulama kapalıyken ------------------------------------------------------------


def test_cevrimdisi_mumlarda_uygulama_isleri_ertelenir(paper):
    """Uygulama kapalıyken borsa tarafı işler (dolum, stop, hedef), uygulama
    tarafı işlemez (süre dolumu iptali). Gecikmiş iş açılışta yapılır."""
    order = place(paper)
    for minute in range(1, 30):
        paper.process_candle("BTCUSDT", quiet(minute), online=False)
    assert paper.ledger.get(order.id).durum == STATUS_PENDING
    events = paper.apply_deferred("BTCUSDT", quiet(29), now=NOW + timedelta(minutes=31))
    assert [item.id for item in events.iptal] == [order.id]
    assert "uygulama kapalıyken" in paper.ledger.get(order.id).iptal_sebebi


def test_cevrimdisi_mumlarda_stop_yine_calisir(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"), online=False)
    events = paper.process_candle(
        "BTCUSDT", candle(2, "59800", "59800", "59350", "59380"), online=False
    )
    assert events.kapanan[0].id == order.id
    assert events.kapanan[0].cikis_sebebi == fills.EXIT_STOP


def test_cevrimdisi_suresi_dolan_pozisyon_acilista_kapanir(paper):
    order = place(paper, meta=OrderMeta(azami_tutma_mum=1))
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"), online=False)
    for minute in range(2, 40):
        paper.process_candle("BTCUSDT", quiet(minute), online=False)
    assert paper.ledger.get(order.id).durum == STATUS_OPEN
    events = paper.apply_deferred("BTCUSDT", quiet(39, "60300"), now=NOW + timedelta(minutes=41))
    closed = events.kapanan[0]
    assert closed.cikis_sebebi == fills.EXIT_TIME
    assert "uygulama kapalıyken" in " ".join(closed.not_listesi)


# --- acil durdurma ve sınır aşımı -------------------------------------------------------


def test_acil_durdur_bekleyeni_iptal_eder_ve_modu_indirir(paper, notifier):
    order = place(paper)
    events = paper.kill_switch(close_positions=False, marks={}, source=SOURCE_UI, now=NOW)
    assert [item.id for item in events.iptal] == [order.id]
    assert paper.modes.get("BTCUSDT") == MODE_ADVICE
    assert any("ACİL DURDUR" in text for text in notifier.texts)
    assert paper.audit.recent(1)[0].tur == "acil_durdur"


def test_acil_durdur_istenirse_pozisyonu_kapatir(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    later = NOW + timedelta(minutes=3)
    events = paper.kill_switch(close_positions=True, marks={"BTCUSDT": D("60100")},
                               source=SOURCE_UI, now=later)
    closed = events.kapanan[0]
    assert closed.id == order.id
    assert closed.cikis_sebebi == fills.EXIT_KILL
    assert D(closed.cikis_fiyati) == D("60087.98")  # 60100 × 0,9998


def test_acil_durdur_pozisyonu_istenmezse_yerinde_birakir(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    paper.kill_switch(close_positions=False, marks={"BTCUSDT": D("60100")},
                      source=SOURCE_UI, now=NOW + timedelta(minutes=3))
    assert paper.ledger.get(order.id).durum == STATUS_OPEN
    # Stop hâlâ çalışıyor.
    events = paper.process_candle("BTCUSDT", candle(5, "59500", "59500", "59300", "59350"))
    assert events.kapanan[0].cikis_sebebi == fills.EXIT_STOP


def test_art_arda_kayip_siniri_asilinca_kagit_islem_durur(paper, notifier):
    relax(paper, art_arda_kayip_limiti=1, kayip_sonrasi_soguma_mum=0)
    place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    events = paper.process_candle("BTCUSDT", candle(2, "59800", "59800", "59350", "59380"))
    assert [item.tur for item in events.sinir_asimlari] == ["art_arda_kayip"]
    assert paper.modes.get("BTCUSDT") == MODE_ADVICE
    assert any("OTOMATİK İŞLEM DURDU" in text for text in notifier.texts)
    # Aynı sınır aynı işlem için iki kez bildirilmez.
    later = NOW + timedelta(minutes=5)
    paper.modes.set("BTCUSDT", MODE_PAPER)
    decision, order = paper.place(intent(), market=market(later), now=later)
    assert order is None
    assert "art_arda_kayip" in [gate.ad for gate in decision.kapali_kapilar]
    # Elle sıfırlama sayacı temizler.
    paper.reset_streak(source=SOURCE_UI, now=later)
    decision, order = paper.place(intent(), market=market(later), now=later)
    assert order is not None, decision.ozet_tr


def test_gunluk_zarar_siniri_bekleyen_girisleri_iptal_eder(paper):
    """İleriye bakış kapısı stopların toplamını sınırın altında tutar; sınırı
    ancak stopun altında açılan bir boşluk aşabilir."""
    relax(paper, gunluk_max_zarar_yuzde=D("1.5"), islem_basi_risk_yuzde=D("0.5"),
          max_es_zamanli_pozisyon=2, kayip_sonrasi_soguma_mum=0)
    paper.modes.set("SOLUSDT", MODE_PAPER)
    first = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    # SOLUSDT'de ikinci bir giriş bekliyor.
    decision, sol = paper.place(
        intent(sembol="SOLUSDT", giris=D("150"), hedef=D("152.5"), stop=D("148.5")),
        market=market(sembol="SOLUSDT", son_fiyat=D("150.2"), kurallar=SOL_RULES),
        now=NOW,
    )
    assert sol is not None, decision.ozet_tr
    # Stopun çok altında açılış: zarar planlanandan büyük.
    events = paper.process_candle("BTCUSDT", candle(2, "57000", "57100", "56900", "57050"))
    assert events.kapanan[0].id == first.id
    assert -D(events.kapanan[0].net_usdt) > D("1.5")
    assert "gunluk_zarar" in [item.tur for item in events.sinir_asimlari]
    assert paper.ledger.get(sol.id).durum == STATUS_CANCELLED
    assert paper.modes.paper_symbols() == ()


# --- elle emir ve performans koruması ------------------------------------------------------


def _round(paper: PaperEngine, start: int, meta: OrderMeta, **changes) -> None:
    """Emir ver, bir sonraki mumda dol, ondan sonrakinde stop ol."""
    now = datetime.fromtimestamp((BASE_MS + start * MINUTE) / 1000, UTC) + timedelta(seconds=30)
    place(paper, now=now, meta=meta, **changes)
    paper.process_candle("BTCUSDT", candle(start + 1, "60050", "60050", "59990", "60000"))
    paper.process_candle("BTCUSDT", candle(start + 2, "59800", "59800", "59350", "59380"))


def test_performansi_bozulan_kural_durdurulur_elle_emir_etkilenmez(paper, notifier):
    relax(paper, art_arda_kayip_limiti=20, gunluk_max_zarar_yuzde=20,
          haftalik_max_dusus_yuzde=50, aylik_max_dusus_yuzde=80,
          kayip_sonrasi_soguma_mum=0, performans_min_islem=5)
    meta = OrderMeta(beklenen_ortalama_yuzde=0.4)
    for index in range(5):
        _round(paper, index * 3, meta)
    assert paper.disabled_rules() == ["kural-1"]
    assert any("KURAL DURDURULDU" in text for text in notifier.texts)

    later = datetime.fromtimestamp((BASE_MS + 20 * MINUTE) / 1000, UTC)
    decision, order = paper.place(intent(), market=market(later), now=later)
    assert order is None
    assert "performans" in [gate.ad for gate in decision.kapali_kapilar]

    manual = intent(kaynak=SOURCE_MANUAL, kural_kimligi=None)
    decision, order = paper.place(manual, market=market(later), now=later)
    assert order is not None, decision.ozet_tr
    assert order.kaynak == SOURCE_MANUAL

    # Elle yeniden açma sınamayı sıfırlar: eski işlemler yeniden sayılmaz.
    paper.cancel(order.id, source=SOURCE_UI)
    paper.enable_rule("kural-1", source=SOURCE_UI, now=later)
    assert paper.disabled_rules() == []
    decision, order = paper.place(intent(), market=market(later), now=later)
    assert order is not None, decision.ozet_tr


def test_elle_emirler_kural_performansina_sayilmaz(paper):
    relax(paper, art_arda_kayip_limiti=20, gunluk_max_zarar_yuzde=20,
          haftalik_max_dusus_yuzde=50, aylik_max_dusus_yuzde=80,
          kayip_sonrasi_soguma_mum=0, performans_min_islem=5)
    meta = OrderMeta(beklenen_ortalama_yuzde=0.4)
    for index in range(5):
        _round(paper, index * 3, meta, kaynak=SOURCE_MANUAL, kural_kimligi=None)
    assert paper.disabled_rules() == []


# --- kullanıcı eylemleri --------------------------------------------------------------------


def test_elle_kapatma_piyasa_fiyatindan(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    closed = paper.close(order.id, mark=D("60300"), source=SOURCE_UI,
                         now=NOW + timedelta(minutes=2))
    assert closed.cikis_sebebi == fills.EXIT_MANUAL
    assert D(closed.cikis_fiyati) == D("60287.94")


def test_iptal_yalnizca_bekleyen_emirde(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    with pytest.raises(ValueError):
        paper.cancel(order.id, source=SOURCE_UI)


def test_acik_is_varken_hesap_sifirlanamaz(paper):
    place(paper)
    with pytest.raises(ValueError):
        paper.reset_account(source=SOURCE_UI, now=NOW)


def test_hesap_sifirlama_yeni_donem_acar_eski_kayit_kalir(paper):
    order = place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    paper.process_candle("BTCUSDT", candle(2, "59800", "59800", "59350", "59380"))
    assert paper.account().nakit_usdt < D("100")
    old_period = paper.account().donem_id
    new_period = paper.reset_account(source=SOURCE_UI, now=NOW + timedelta(minutes=5))
    assert new_period != old_period
    view = paper.account()
    assert view.nakit_usdt == D("100")
    assert view.coinler == {}
    assert paper.ledger.get(order.id).durum == STATUS_CLOSED


def test_ozsermaye_fiyat_yoksa_hesaplanmaz(paper):
    place(paper)
    paper.process_candle("BTCUSDT", candle(1, "60050", "60050", "59990", "60000"))
    view = paper.account()
    assert view.ozsermaye_usdt is None
    assert view.eksik_fiyat == ("BTCUSDT",)
    view = paper.account({"BTCUSDT": D("60000")})
    assert view.ozsermaye_usdt is not None
    # Komisyon coinden düştüğü için aynı fiyatta özsermaye biraz azalır.
    assert view.ozsermaye_usdt < D("100")
