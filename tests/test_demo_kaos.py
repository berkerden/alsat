"""Faz 5 kabul ölçütü: **kaos testleri** (SPEC.md §10 Faz 5).

Demo yürütücüsü sahte bir Binance'e (``fake_binance.py``) karşı çalışır. Sahte
borsa her isteğin Ed25519 imzasını ve zaman damgasını gerçekten doğrular,
emirleri eşleştirir, bakiyeyi kilitler ve hesap akışını yayınlar. Testler
yürütücünün ``tick``'ini elle çağırır; saat de elle ilerletilir.

Her testte aynı değişmezler denetlenir (``check_invariants``):

* Aynı emir iki kez gönderilmez (her istemci kimliği borsada bir kez).
* Kapanan pozisyonun kaydı borsadaki dolumlarla birebir tutar.
* Açık bir pozisyonda elde coin varsa ya borsada canlı bir stop vardır ya da
  pozisyon "korumasız" olarak işaretlidir ve süresi ölçülmektedir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fake_binance import START_USDT, FakeBinance, FakeBookStream, FakeUserStream
from test_kagit_islem import COSTS, NOW, RULES, intent, market

from albsat.core.audit import SOURCE_UI
from albsat.core.clock import to_ms
from albsat.data.live import LiveMarket
from albsat.data.store import KlineStore
from albsat.exchange.keys import SecretText
from albsat.exchange.signed import StoredKey, generate_keypair
from albsat.exchange.trading import DemoTrader
from albsat.execution.executor import DemoExecutor
from albsat.execution.ledger import (
    EXIT_DUST,
    EXIT_KILL,
    EXIT_STOP,
    EXIT_TARGET,
    EXIT_TIME,
    LEG_NEVER,
    POS_CANCELLED,
    POS_CLOSED,
    POS_PENDING,
    POS_PROTECTED,
    POS_REJECTED,
    POS_UNKNOWN,
    POS_UNPROTECTED,
    ROLE_STOP,
)
from albsat.modes.state import MODE_ADVICE, MODE_DEMO
from albsat.notify.base import MemoryNotifier
from albsat.paper.engine import OrderMeta, PaperEngine
from albsat.risk.limits import RiskLimits

D = Decimal
API_KEY = "demoAnahtarKimligi0123456789abcdefABCDEF0123456789"
PRIVATE, PUBLIC_PEM = generate_keypair()
KEY = StoredKey(SecretText(API_KEY), PRIVATE)
BTC = "BTCUSDT"


class Clock:
    """Duvar saati ve tekdüze saat; testte elle ilerler."""

    def __init__(self) -> None:
        self.t = NOW
        self.mono = 10_000.0
        #: Bu bilgisayarın saatinin borsadan farkı (ms).
        self.skew_ms = 0

    def now(self):  # noqa: ANN201
        return self.t

    def monotonic(self) -> float:
        return self.mono

    def ms(self) -> int:
        return to_ms(self.t)

    def local_ms(self) -> int:
        return self.ms() + self.skew_ms

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)
        self.mono += seconds


@dataclass
class Rig:
    root: Path
    clock: Clock
    fake: FakeBinance
    engine: PaperEngine
    notifier: MemoryNotifier
    executor: DemoExecutor
    streams: list[FakeUserStream] = field(default_factory=list)

    @property
    def stream(self) -> FakeUserStream:
        return self.streams[-1]

    def tick(self, times: int = 1, seconds: float = 1.0) -> None:
        for _ in range(times):
            self.clock.advance(seconds)
            self.executor.tick()

    def place(self, reprice: bool = False, **changes: Any):  # noqa: ANN201
        now = self.clock.now()
        return self.executor.place(intent(**changes), market=market(now), now=now,
                                   meta=OrderMeta(kural_etiketi="test"), reprice=reprice)

    def position(self, position_id: int):  # noqa: ANN201
        return self.executor.ledger.position(position_id)

    def texts(self) -> str:
        return "\n".join(self.notifier.texts)


def build(root: Path, *, clock: Clock | None = None, fake: FakeBinance | None = None,
          usdt: Decimal = START_USDT, bootstrap: bool = True) -> Rig:
    clock = clock or Clock()
    fake = fake or FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY,
                               usdt=usdt)
    notifier = MemoryNotifier()
    engine = PaperEngine(root, symbols=(BTC,), notifier=notifier,
                         costs_for=lambda symbol: COSTS, rules_for=lambda symbol: RULES)
    engine.modes.start_session()
    streams: list[FakeUserStream] = []

    def stream_factory(**kwargs: Any) -> FakeUserStream:
        stream = FakeUserStream(fake, kwargs["on_event"], kwargs.get("on_state"),
                                kwargs.get("server_time_ms"))
        streams.append(stream)
        return stream

    trader = DemoTrader(KEY, opener=fake, time_ms=clock.local_ms)
    executor = DemoExecutor(
        root, symbols=(BTC,), engine=engine,
        live_market=LiveMarket(KlineStore(root), clock=clock.now),
        demo_market=LiveMarket(KlineStore(root), clock=clock.now),
        notifier=notifier, trader=trader, public=fake, stream_factory=stream_factory,
        book_stream_factory=lambda on_event: FakeBookStream(fake, on_event),
        clock=clock.now, monotonic=clock.monotonic,
    )
    rig = Rig(root, clock, fake, engine, notifier, executor, streams)
    if bootstrap:
        assert executor.bootstrap(), executor.health.son_hata or executor.health.hazirlik_sorunu
        bid, ask = fake.book[BTC]
        fake.set_book(BTC, bid, ask)
        engine.set_mode(BTC, MODE_DEMO, source=SOURCE_UI, now=clock.now())
    return rig


@pytest.fixture
def rig(tmp_path) -> Rig:
    return build(tmp_path)


def placed(rig: Rig, **changes: Any):  # noqa: ANN201
    result = rig.place(**changes)
    assert result.pozisyon is not None, result.mesaj
    assert result.pozisyon.durum == POS_PENDING, (result.pozisyon.durum, result.mesaj)
    return result.pozisyon


def check_invariants(rig: Rig) -> None:
    fake = rig.fake
    ledger = rig.executor.ledger
    # 1) Her istemci kimliği borsaya en fazla bir kez gönderildi.
    sent: list[str] = []
    for params in fake.posts():
        for key in ("workingClientOrderId", "pendingAboveClientOrderId",
                    "pendingBelowClientOrderId", "aboveClientOrderId", "belowClientOrderId",
                    "newClientOrderId"):
            if key in params:
                sent.append(params[key])
    assert len(sent) == len(set(sent)), f"aynı kimlik iki kez gönderildi: {sent}"
    # 2) Kayıttaki her dolum borsada var ve miktarı aynı.
    trades = {(trade["symbol"], trade["id"]): trade for trade in fake.trades}
    for position in ledger.recent(1000):
        for fill in ledger.fills(position.id):
            trade = trades.get((fill.sembol, fill.islem_kimligi))
            assert trade is not None, f"borsada olmayan dolum: {fill}"
            assert D(trade["qty"]) == fill.dec("miktar")
    # 3) Kapanan pozisyonların dolumları eksiksiz (borsadaki her albsat dolumu kayıtta).
    recorded = {(fill.sembol, fill.islem_kimligi) for position in ledger.recent(1000)
                for fill in ledger.fills(position.id)}
    ours = {order.order_id for order in fake.orders_by_prefix()}
    for trade in fake.trades:
        if trade["orderId"] in ours:
            owner = next(order for order in fake.orders.values()
                         if order.order_id == trade["orderId"])
            leg = ledger.leg(owner.client_id)
            if leg is not None:
                position = ledger.position(leg.pozisyon_id)
                if position is not None and position.bitti:
                    assert (trade["symbol"], trade["id"]) in recorded
    # 4) Açık pozisyonda coin varsa: canlı stop ya da ölçülen korumasızlık.
    for position in ledger.active():
        view = rig.executor._view(position)
        if view.held > 0 and view.sellable > 0:
            live_stop = [leg for leg in view.legs if leg.rol == ROLE_STOP
                         and fake.by_client.get(leg.istemci_kimligi)
                         and fake.order(leg.istemci_kimligi).status in ("NEW",
                                                                          "PARTIALLY_FILLED")]
            assert live_stop or position.korumasiz_baslangic_ms is not None \
                or position.cikis_istegi, position


def fill_entry(rig: Rig, position_id: int, qty: str | None = None) -> None:
    del position_id
    rig.fake.move(BTC, "60000", qty)


# --- mutlu yollar ---------------------------------------------------------------


def test_hedefe_ulasan_islem_kapanir_ve_sonuc_borsayla_tutar(rig):
    position = placed(rig)
    fake = rig.fake
    assert len(fake.posts("/api/v3/orderList/otoco")) == 1
    entry = fake.order(f"albsat-demo-{position.jeton}-G1")
    assert entry.type == "LIMIT_MAKER" and entry.side == "BUY"
    assert fake.order(f"albsat-demo-{position.jeton}-S1").status == "PENDING_NEW"

    fill_entry(rig, position.id)
    rig.tick()
    current = rig.position(position.id)
    assert current.durum == POS_PROTECTED, current.aciklama
    assert "DOLDU (demo)" in rig.texts()

    fake.move(BTC, "60900")
    rig.tick()
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED
    assert closed.cikis_sebebi == EXIT_TARGET
    # Sonuç borsadaki dolumlardan: alış 60000'den, satış 60900'den, komisyon %0,1.
    qty = D(closed.miktar)
    sold = D(closed.bekleyen_miktar)
    bought_net = qty * D("0.999")
    expected = (sold * D("60900") * D("0.999")) - qty * D("60000") \
        + (bought_net - sold) * D(closed.cikis_fiyati)
    assert abs(D(closed.net_usdt) - expected) < D("0.0000001")
    assert D(closed.net_usdt) > 0
    assert closed.korumasiz_toplam_ms == 0
    check_invariants(rig)


def test_stopa_ulasan_islem_kapanir(rig):
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.fake.move(BTC, "59390")
    rig.tick()
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED and closed.cikis_sebebi == EXIT_STOP
    assert D(closed.net_usdt) < 0
    assert D(closed.cikis_fiyati) == D("59390")
    check_invariants(rig)


def test_giris_gecerlilik_suresinde_dolmazsa_iptal_edilir(rig):
    position = placed(rig)
    rig.clock.advance(15 * 60)
    rig.tick(2)
    current = rig.position(position.id)
    assert current.durum == POS_CANCELLED
    entry = rig.fake.order(f"albsat-demo-{position.jeton}-G1")
    assert entry.status == "CANCELED"
    assert rig.fake.balances["USDT"] == [D("5000"), D("0")]
    check_invariants(rig)


# --- kısmi dolumlar -------------------------------------------------------------


def test_kismi_giris_dolumu_suresi_bitince_kalani_iptal_edip_dolani_korur(rig):
    """OTOCO'nun hedef/stopu giriş TAMAMEN dolmadan borsaya konmaz. Yarım dolan
    girişte coin korumasızdır; en fazla ``korumasiz_azami_saniye`` beklenir."""
    position = placed(rig)
    fill_entry(rig, position.id, qty="0.00050")
    rig.tick()
    current = rig.position(position.id)
    assert current.durum == "kismi"
    assert current.korumasiz_baslangic_ms is not None
    assert "KISMEN DOLDU" in rig.texts()
    check_invariants(rig)
    # 19 saniye: hâlâ bekliyor; giriş borsada canlı.
    rig.tick(18)
    assert rig.fake.order(f"albsat-demo-{position.jeton}-G1").status == "PARTIALLY_FILLED"
    # 20. saniye: liste iptal, dolan kısım için yeni OCO.
    rig.tick(3)
    current = rig.position(position.id)
    assert rig.fake.order(f"albsat-demo-{position.jeton}-G1").status == "CANCELED"
    assert current.durum == POS_PROTECTED, current.aciklama
    oco = rig.fake.posts("/api/v3/orderList/oco")
    assert len(oco) == 1
    assert D(oco[0]["quantity"]) == D("0.00049")  # 0,0005 × 0,999 aşağı yuvarlanmış
    assert 19_000 <= current.korumasiz_toplam_ms <= 23_000
    assert "KORUMASIZ" in rig.texts() and "KORUMA KURULDU" in rig.texts()
    rig.fake.move(BTC, "60900")
    rig.tick()
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED and closed.cikis_sebebi == EXIT_TARGET
    check_invariants(rig)


def test_kismi_girisin_kalani_dolarsa_otoco_korumasi_devreye_girer(rig):
    position = placed(rig)
    fill_entry(rig, position.id, qty="0.00050")
    rig.tick(5)
    rig.fake.move(BTC, "60000")  # kalanı da doldu
    rig.tick()
    current = rig.position(position.id)
    assert current.durum == POS_PROTECTED
    assert not rig.fake.posts("/api/v3/orderList/oco")
    assert 5_000 <= current.korumasiz_toplam_ms <= 7_000
    check_invariants(rig)


def test_hedef_kismen_dolunca_stop_duser_kalan_yeniden_korunur(rig):
    """OCO'da hedef kısmen dolunca borsa stopu kaldırır. Kalan coin stopsuz kalır."""
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.fake.move(BTC, "60900", qty="0.00060")
    rig.tick()
    current = rig.position(position.id)
    assert rig.fake.order(f"albsat-demo-{position.jeton}-S1").status == "EXPIRED"
    assert current.durum == POS_UNPROTECTED
    rig.fake.set_book(BTC, "60500", "60500.01")
    rig.tick(22)
    current = rig.position(position.id)
    assert current.durum == POS_PROTECTED, current.aciklama
    oco = rig.fake.posts("/api/v3/orderList/oco")
    assert len(oco) == 1
    remaining = D(position.bekleyen_miktar) - D("0.00060")
    assert D(oco[0]["quantity"]) == remaining
    rig.fake.move(BTC, "59390")
    rig.tick()
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED
    assert closed.cikis_sebebi in (EXIT_TARGET, EXIT_STOP)
    check_invariants(rig)


def test_korumasizken_fiyat_stopun_altina_inerse_korumali_satisla_cikar(rig):
    position = placed(rig)
    fill_entry(rig, position.id, qty="0.00050")
    rig.tick()
    rig.fake.set_book(BTC, "59300", "59300.01")
    rig.tick(22)
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED, closed.aciklama
    assert closed.cikis_sebebi == EXIT_STOP
    ioc = rig.fake.posts("/api/v3/order")
    assert len(ioc) == 1
    assert ioc[0]["timeInForce"] == "IOC" and ioc[0]["type"] == "LIMIT"
    # En iyi alışın en fazla %0,5 altı.
    assert D(ioc[0]["price"]) == D("59003.50")
    check_invariants(rig)


def test_ince_defterde_cikis_dolana_kadar_yeniden_denenir(rig):
    position = placed(rig)
    fill_entry(rig, position.id, qty="0.00050")
    rig.tick()
    rig.fake.set_book(BTC, "59300", "59300.01")
    rig.fake.depth[BTC] = D("0")
    rig.tick(40)
    current = rig.position(position.id)
    assert current.durum == "cikiliyor"
    attempts = len(rig.fake.posts("/api/v3/order"))
    assert attempts >= 10
    assert "ÇIKIŞ DOLMUYOR" in rig.texts()
    rig.fake.depth[BTC] = None
    rig.tick(3)
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED
    check_invariants(rig)


# --- sonucu bilinmeyen emir -----------------------------------------------------------


@pytest.mark.parametrize("kind", ["kopuk_sonra", "500_sonra", "-1007_sonra"])
def test_yanit_kaybolsa_da_borsaya_ulasan_emir_bulunur_ve_tekrarlanmaz(rig, kind):
    rig.fake.fail_next("POST", "/api/v3/orderList/otoco", kind)
    result = rig.place()
    assert result.pozisyon is not None
    assert result.pozisyon.durum == POS_UNKNOWN
    assert len(rig.fake.open_orders(BTC)) == 3  # borsa emri aldı
    # Belirsizken yeni emir açılmaz.
    second = rig.place()
    assert second.pozisyon is None
    assert any(gate.ad == "belirsiz_emir" and not gate.gecti for gate in second.karar.kapilar)
    rig.tick()
    current = rig.position(result.pozisyon.id)
    assert current.durum == POS_PENDING
    assert len(rig.fake.posts("/api/v3/orderList/otoco")) == 1
    fill_entry(rig, current.id)
    rig.tick()
    assert rig.position(current.id).durum == POS_PROTECTED
    check_invariants(rig)


@pytest.mark.parametrize("kind", ["kopuk_once", "500_once"])
def test_borsaya_ulasmayan_emir_recvwindow_sonrasi_ulasmadi_sayilir(rig, kind):
    rig.fake.fail_next("POST", "/api/v3/orderList/otoco", kind)
    result = rig.place()
    assert result.pozisyon is not None and result.pozisyon.durum == POS_UNKNOWN
    rig.tick(3)
    # recvWindow (5 sn) + pay dolmadan "yok" cevabı kesin sayılmaz.
    assert rig.position(result.pozisyon.id).durum == POS_UNKNOWN
    rig.tick(10)
    current = rig.position(result.pozisyon.id)
    assert current.durum == POS_REJECTED
    assert "ulaşmadı" in (current.aciklama or "")
    legs = rig.executor.ledger.legs(current.id)
    assert {leg.durum for leg in legs} == {LEG_NEVER}
    assert not rig.fake.posts("/api/v3/orderList/otoco")  # hiç işlenmedi, yeniden de yok
    check_invariants(rig)


def test_iptal_isteginin_yaniti_kaybolursa_sorgulanir(rig):
    position = placed(rig)
    rig.fake.fail_next("DELETE", "/api/v3/orderList", "kopuk_sonra")
    rig.clock.advance(15 * 60)
    rig.tick(8)
    assert rig.position(position.id).durum == POS_CANCELLED
    check_invariants(rig)


def test_bekleyen_bacaklar_sorguda_gorunmese_de_giris_dolunca_benimsenir(rig):
    """OTOCO'nun bekleyen bacakları sorguda bulunamazsa "ulaşmadı" sayılır; giriş
    dolup borsada belirdiklerinde borsa haklıdır, ikinci bir koruma kurulmaz."""
    rig.fake.pending_queryable = False
    rig.fake.fail_next("POST", "/api/v3/orderList/otoco", "kopuk_sonra")
    result = rig.place()
    rig.tick(15)
    current = rig.position(result.pozisyon.id)
    assert current.durum == POS_PENDING
    fill_entry(rig, current.id)
    rig.tick(2)
    assert rig.position(current.id).durum == POS_PROTECTED
    assert not rig.fake.posts("/api/v3/orderList/oco")
    check_invariants(rig)


# --- akış kopması, çökme, uyku ----------------------------------------------------------


def test_akis_kopukken_dolumlar_rest_ile_bulunur(rig):
    position = placed(rig)
    rig.stream.drop()
    fill_entry(rig, position.id)
    rig.fake.move(BTC, "60900")
    assert rig.fake.lost_events > 0
    # Akış kopukken yeni emir açılmaz.
    assert rig.executor.not_ready_reason() is not None
    rig.tick(16)
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED, closed.aciklama
    assert closed.cikis_sebebi == EXIT_TARGET
    check_invariants(rig)


def test_uygulama_kismi_dolum_ortasinda_coker_yeniden_acilinca_devam_eder(tmp_path):
    first = build(tmp_path)
    position = placed(first)
    first.stream.drop()  # uygulama kapandı: olaylar kimseye ulaşmıyor
    first.fake.move(BTC, "60000", "0.00050")
    first.clock.advance(120)
    # Yeniden açılış: aynı veritabanı, aynı borsa, yeni yürütücü.
    second = build(tmp_path, clock=first.clock, fake=first.fake)
    current = second.position(position.id)
    assert D(second.executor.ledger.leg(f"albsat-demo-{position.jeton}-G1").dolan) \
        == D("0.00050")
    assert second.executor.ledger.fills(position.id)
    second.tick(2)
    current = second.position(position.id)
    assert current.durum in ("kismi", POS_UNPROTECTED)
    second.tick(22)
    current = second.position(position.id)
    assert current.durum == POS_PROTECTED, current.aciklama
    check_invariants(second)


def test_uyku_sonrasi_uzlastirma_kacan_stopu_bulur(rig):
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.stream.drop()
    rig.fake.move(BTC, "59390")  # Mac uyurken stop çalıştı
    # Duvar saati 10 dk ilerledi, tekdüze saat ilerlemedi (macOS uykusu).
    rig.clock.t += timedelta(minutes=10)
    rig.executor.tick()
    assert rig.stream.reconnects >= 1
    rig.tick()
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED and closed.cikis_sebebi == EXIT_STOP
    check_invariants(rig)


def test_saat_farki_1021_olunca_esitlenip_bir_kez_yeniden_denenir(rig):
    rig.clock.skew_ms = 3_000  # bu bilgisayarın saati 3 sn ileride
    position = placed(rig)
    assert len(rig.fake.posts("/api/v3/orderList/otoco")) == 1
    assert rig.executor.trader.offset_ms == -3_000
    check_invariants(rig)
    assert position.durum == POS_PENDING


# --- limitler ---------------------------------------------------------------------------


def test_429_emri_gondermez_ve_bekleme_suresince_istek_atmaz(rig):
    rig.fake.fail_next("POST", "/api/v3/orderList/otoco", "429")
    result = rig.place()
    assert result.pozisyon is not None and result.pozisyon.durum == POS_REJECTED
    assert "429" in result.mesaj
    before = len(rig.fake.requests)
    rig.tick()
    assert len(rig.fake.requests) == before  # bekleme süresi dolmadan istek yok
    check_invariants(rig)


def test_418_ip_engelinde_yeni_emir_acilmaz(rig):
    rig.fake.fail_next("POST", "/api/v3/orderList/otoco", "418")
    rig.place()
    reason = rig.executor.not_ready_reason() or ""
    assert "418" in reason
    before = len(rig.fake.requests)
    second = rig.place()
    assert second.pozisyon is None
    assert len(rig.fake.requests) == before


def test_limit_maker_reddinde_kural_emri_en_iyi_alisa_cekilir(rig):
    # Demo defteri akıştan habersiz aşağı indi: giriş artık hemen eşleşir.
    rig.fake.set_book(BTC, "59989.99", "59990.00", publish=False)
    result = rig.place(reprice=True)
    assert result.pozisyon is not None, result.mesaj
    current = rig.position(result.pozisyon.id)
    assert current.durum == POS_PENDING, current.aciklama
    assert D(current.giris) == D("59989.99")
    assert current.yeniden_fiyatlama == 1
    otoco = rig.fake.posts("/api/v3/orderList/otoco")
    assert len(otoco) == 2 and otoco[0]["workingClientOrderId"].endswith("-G1")
    assert otoco[1]["workingClientOrderId"].endswith("-G2")
    check_invariants(rig)


def test_limit_maker_reddinde_elle_emir_yeniden_fiyatlanmaz(rig):
    rig.fake.set_book(BTC, "59989.99", "59990.00", publish=False)
    result = rig.place(reprice=False, kaynak="elle", kural_kimligi=None)
    assert result.pozisyon is not None
    assert rig.position(result.pozisyon.id).durum == POS_REJECTED
    assert "hemen eşleşeceği" in result.mesaj


def test_demo_hesabinda_bakiye_azsa_emir_bakiyeyle_sinirlanir(tmp_path):
    rig = build(tmp_path, usdt=D("20"))
    position = placed(rig)
    assert D(position.tutar_usdt) <= D("20")
    check_invariants(rig)


def test_demo_hesabinda_bakiye_yetmezse_emir_acilmaz(tmp_path):
    rig = build(tmp_path, usdt=D("4"))
    result = rig.place()
    assert result.pozisyon is None
    assert not rig.fake.posts()


# --- borsanın kendiliğinden bitirdiği stop ----------------------------------------------


def test_suresi_dolan_stop_yeniden_kurulur(rig):
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.fake.expire(f"albsat-demo-{position.jeton}-S1")
    rig.tick(2)
    current = rig.position(position.id)
    assert current.durum == POS_PROTECTED, current.aciklama
    assert len(rig.fake.posts("/api/v3/orderList/oco")) == 1
    check_invariants(rig)


# --- kullanıcı eylemleri ------------------------------------------------------------------


def test_acil_durdur_bekleyen_girisi_borsada_iptal_eder(rig):
    position = placed(rig)
    rig.engine.kill_switch(close_positions=False, marks={}, source=SOURCE_UI,
                           now=rig.clock.now())
    rig.executor.kill_switch(close_positions=False, source=SOURCE_UI)
    rig.tick()
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    assert rig.position(position.id).durum == POS_CANCELLED
    assert rig.fake.order(f"albsat-demo-{position.jeton}-G1").status == "CANCELED"
    check_invariants(rig)


def test_acil_durdur_kapat_acik_pozisyonu_korumali_satar(rig):
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.engine.kill_switch(close_positions=True, marks={}, source=SOURCE_UI,
                           now=rig.clock.now())
    count = rig.executor.kill_switch(close_positions=True, source=SOURCE_UI)
    assert count == 1
    rig.tick(3)
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED and closed.cikis_sebebi == EXIT_KILL
    assert rig.fake.order(f"albsat-demo-{position.jeton}-S1").status == "CANCELED"
    check_invariants(rig)


def test_acil_durdur_kapatmadan_stop_ve_hedef_yerinde_kalir(rig):
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.engine.kill_switch(close_positions=False, marks={}, source=SOURCE_UI,
                           now=rig.clock.now())
    rig.executor.kill_switch(close_positions=False, source=SOURCE_UI)
    rig.tick(2)
    assert rig.position(position.id).durum == POS_PROTECTED
    assert rig.fake.order(f"albsat-demo-{position.jeton}-S1").status == "NEW"


def test_azami_tutma_suresi_dolunca_kapanir(rig):
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.clock.advance(4 * 15 * 60)
    rig.tick(4)
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED and closed.cikis_sebebi == EXIT_TIME
    check_invariants(rig)


# --- uzlaştırma: başkasının emirleri ---------------------------------------------------------


def test_elle_verilen_emirlere_dokunulmaz(rig):
    manual = rig.fake.add_manual_order(BTC, "web_elle_123", "BUY", "50000.00", "0.00100")
    summary = rig.executor.reconcile("test", full=True)
    assert "elle verilmiş emirler" in summary
    assert manual.status == "NEW"
    assert not [params for method, path, params in rig.fake.requests
                if method == "DELETE" and "web_elle_123" in str(params)]


def test_kayitta_olmayan_albsat_alisi_iptal_edilir_satisi_birakilir(rig):
    buy = rig.fake.add_manual_order(BTC, "albsat-demo-yetim00-G1", "BUY", "50000.00",
                                    "0.00100")
    rig.fake.balances["BTC"] = [D("0.01"), D("0")]
    sell = rig.fake.add_manual_order(BTC, "albsat-demo-yetim00-H1", "SELL", "70000.00",
                                     "0.00100")
    rig.executor.reconcile("test", full=True)
    assert buy.status == "CANCELED"
    assert sell.status == "NEW"


# --- risk sınırı -------------------------------------------------------------------------------


def test_gunluk_zarar_siniri_asilinca_demo_durur(rig):
    values = RiskLimits().to_json()
    values.update({"gunluk_max_zarar_yuzde": "2.0", "kayip_sonrasi_soguma_mum": 0})
    rig.engine.limit_store.write(RiskLimits.from_json(values))
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.fake.move(BTC, "58000")  # boşluklu düşüş: piyasa stopu stopun çok altında dolar
    rig.tick()
    assert rig.position(position.id).cikis_sebebi == EXIT_STOP
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    assert "OTOMATİK İŞLEM DURDU" in rig.texts()
    result = rig.place()
    assert result.pozisyon is None


# --- küsurat ---------------------------------------------------------------------------------


def test_kusurat_bir_sonraki_islemin_satisina_eklenir(rig):
    first = placed(rig)
    fill_entry(rig, first.id)
    rig.tick()
    rig.fake.move(BTC, "60900")
    rig.tick()
    closed = rig.position(first.id)
    dust = D(closed.toz_degisimi)
    assert dust > 0
    rig.fake.set_book(BTC, "60049.99", "60050.00")
    rig.clock.advance(60)
    second = placed(rig)
    assert D(second.onceki_toz) == dust
    expected = RULES.round_quantity(D(second.miktar) * D("0.999") + dust)
    assert D(second.bekleyen_miktar) == expected
    check_invariants(rig)


def test_satilamayacak_kadar_kucuk_kismi_dolum_kusurat_olarak_kapanir(rig):
    position = placed(rig)
    fill_entry(rig, position.id, qty="0.00005")  # 3 USDT: en küçük emir tutarının altında
    rig.tick(25)
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED, closed.aciklama
    assert closed.cikis_sebebi == EXIT_DUST
    assert "küsurat" in rig.texts()
    check_invariants(rig)


def test_islemler_dolum_csv_sinda_gorunur(rig):
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    rig.fake.move(BTC, "60900")
    rig.tick()
    text = rig.executor.fills_csv()
    lines = [line for line in text.splitlines() if line]
    assert lines[0].lstrip("﻿").startswith("tarih_istanbul;")
    assert len(lines) == 3  # alış + hedef satışı
    assert "ALIŞ" in lines[1] and "SATIŞ" in lines[2]

