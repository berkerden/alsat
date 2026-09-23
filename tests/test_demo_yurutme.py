"""Faz 5: Demo yürütmenin parçaları (kaos testlerinin dışında kalanlar).

* İstemci güvenlik kuralları: yalnızca Demo ortamı, izin listesi, önek.
* Kimlikler, planlayıcı (yuvarlama, satış miktarı), hata sınıfları.
* Hesap akışı: abonelik imzası, olayların okunması, yeniden bağlanma.
* Ayarlar ve ``config/default.yaml`` aynası.
* Kurulum (anahtar yoksa), arayüz uçları, Telegram komutları.
"""
from __future__ import annotations

import base64
import json
import queue
import re
import time
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fake_binance import exchange_info
from fastapi.testclient import TestClient
from test_demo_kaos import API_KEY, BTC, KEY, PUBLIC_PEM, Rig, build, fill_entry, placed

from albsat.api.app import AppState, create_app
from albsat.api.runtime import Runtime
from albsat.core.audit import SOURCE_UI
from albsat.core.filters import SymbolRules
from albsat.exchange.endpoints import Environment
from albsat.exchange.trading import (
    ALLOWED,
    CLIENT_PREFIX,
    DemoTrader,
    ExchangeError,
    OutcomeUnknown,
    TradingRefused,
)
from albsat.exchange.user_stream import (
    BalanceUpdate,
    ListUpdate,
    OrderUpdate,
    StreamNotice,
    UserStream,
    parse_user_message,
    signature_payload,
)
from albsat.execution import errors, ids
from albsat.execution.ledger import POS_CLOSED, POS_PROTECTED
from albsat.execution.planner import plan_exit, plan_oco, plan_otoco, sell_quantity
from albsat.execution.service import NO_KEY, OFFLINE, build_executor
from albsat.execution.settings import STOP_LIMIT, ExecutionSettings, SettingsError
from albsat.modes.state import MODE_ADVICE, MODE_DEMO
from albsat.notify.base import MemoryNotifier
from albsat.notify.commands import build_handler
from albsat.paper.engine import PaperEngine

D = Decimal
ROOT = Path(__file__).resolve().parents[1]
RULES = SymbolRules.from_exchange_info(exchange_info([BTC])["symbols"][0])
YAZ = {"X-Albsat-Istek": "1"}


# --- istemci güvenliği ---------------------------------------------------------------


def test_emir_istemcisi_yalnizca_demo_ortaminda_kurulur():
    for environment in (Environment.LIVE, Environment.TESTNET):
        with pytest.raises(TradingRefused):
            DemoTrader(KEY, environment=environment)
    trader = DemoTrader(KEY)
    assert trader.base == "https://demo-api.binance.com"
    assert "demoAnahtar" not in repr(trader)


def test_izin_listesinde_tehlikeli_uc_yok():
    paths = {path for _, path in ALLOWED}
    assert ("DELETE", "/api/v3/openOrders") not in ALLOWED
    assert not any(path.startswith("/sapi") for path in paths)
    assert not any("withdraw" in path or "margin" in path or "futures" in path
                   for path in paths)
    trader = DemoTrader(KEY, opener=lambda *a, **k: pytest.fail("istek gönderilmemeli"))
    with pytest.raises(TradingRefused):
        trader.request("DELETE", "/api/v3/openOrders", {"symbol": BTC})
    with pytest.raises(TradingRefused):
        trader.request("POST", "/sapi/v1/capital/withdraw/apply", {})


def test_baskasinin_emrine_dokunulmaz_ve_emir_tipi_sinirli():
    trader = DemoTrader(KEY, opener=lambda *a, **k: pytest.fail("istek gönderilmemeli"))
    with pytest.raises(TradingRefused):
        trader.cancel_order(BTC, "web_elle_123")
    with pytest.raises(TradingRefused):
        trader.cancel_order_list(BTC, "baskasi")
    good = plan_otoco(rules=RULES, token="abc", attempt=1, entry=D("60000"),
                      target=D("60900"), stop=D("59400"), quantity=D("0.001"),
                      fee_rate=D("0.001"), carried_dust=D("0"),
                      settings=ExecutionSettings()).params
    for key, value in (("workingType", "LIMIT"), ("workingSide", "SELL"),
                       ("pendingBelowType", "TAKE_PROFIT"),
                       ("workingClientOrderId", "kimliksiz")):
        with pytest.raises(TradingRefused):
            trader.place_otoco({**good, key: value})
    with pytest.raises(TradingRefused):
        trader.place_exit({"symbol": BTC, "side": "BUY", "type": "LIMIT",
                           "timeInForce": "IOC", "newClientOrderId": f"{CLIENT_PREFIX}x-C1"})
    with pytest.raises(TradingRefused):
        trader.place_exit({"symbol": BTC, "side": "SELL", "type": "MARKET",
                           "timeInForce": "IOC", "newClientOrderId": f"{CLIENT_PREFIX}x-C1"})


def test_okuma_isteginde_ag_hatasi_belirsiz_sayilmaz_emirde_sayilir(tmp_path):
    rig = build(tmp_path)
    trader = rig.executor.trader
    rig.fake.fail_next("GET", "/api/v3/openOrders", "kopuk_once")
    with pytest.raises(ExchangeError) as read_error:
        trader.open_orders(BTC)
    assert read_error.value.status == 0
    rig.fake.fail_next("DELETE", "/api/v3/order", "kopuk_once")
    with pytest.raises(OutcomeUnknown) as write_error:
        trader.cancel_order(BTC, f"{CLIENT_PREFIX}yok-G1")
    assert write_error.value.sent_ms > 0


def test_imza_gercekten_dogrulaniyor(tmp_path):
    rig = build(tmp_path)
    other = DemoTrader(type(KEY)(KEY.api_key, __import__(
        "albsat.exchange.signed", fromlist=["generate_keypair"]).generate_keypair()[0]),
        opener=rig.fake, time_ms=rig.clock.local_ms)
    with pytest.raises(ExchangeError) as error:
        other.account()
    assert error.value.code == -1022


# --- kimlikler ---------------------------------------------------------------------------


def test_kimlikler_onekli_kisa_ve_denemeye_gore_farkli():
    token = ids.new_token()
    names = ids.otoco_ids(token, 1)
    again = ids.otoco_ids(token, 2)
    oco = ids.oco_ids(token, 1)
    every = [*names.values(), *again.values(), *oco.values(), ids.exit_id(token, 1)]
    assert len(every) == len(set(every))
    for name in every:
        assert name.startswith(CLIENT_PREFIX) and ids.is_ours(name)
        assert re.fullmatch(r"[a-zA-Z0-9\-_]{1,36}", name)
        assert ids.token_of(name) == token
    assert not ids.is_ours("web_123")
    assert len({ids.new_token() for _ in range(200)}) == 200


# --- planlayıcı -------------------------------------------------------------------------------


def test_otoco_plani_yuvarlar_ve_satis_miktarini_komisyondan_sonra_hesaplar():
    plan = plan_otoco(rules=RULES, token="abc", attempt=1, entry=D("60000.004"),
                      target=D("60900.001"), stop=D("59400.009"), quantity=D("0.001666"),
                      fee_rate=D("0.001"), carried_dust=D("0.000004"),
                      settings=ExecutionSettings())
    assert plan.gecerli, plan.sorunlar
    assert plan.params["workingPrice"] == "60000"          # alış aşağı
    assert plan.params["pendingAbovePrice"] == "60900.01"  # satış yukarı
    assert plan.params["pendingBelowStopPrice"] == "59400"  # stop aşağı
    assert plan.params["workingQuantity"] == "0.00166"
    # 0,00166 × 0,999 + 0,000004 = 0,00166234 → 0,00166
    assert plan.params["pendingQuantity"] == "0.00166"
    assert "pendingBelowPrice" not in plan.params


def test_limitli_stop_plani_limit_fiyatini_stopun_altina_koyar():
    settings = ExecutionSettings(stop_tipi=STOP_LIMIT, stop_limit_ofset_yuzde=D("0.5"))
    plan = plan_otoco(rules=RULES, token="abc", attempt=1, entry=D("60000"),
                      target=D("60900"), stop=D("59400"), quantity=D("0.002"),
                      fee_rate=D("0.001"), carried_dust=D("0"), settings=settings)
    assert plan.params["pendingBelowType"] == "STOP_LOSS_LIMIT"
    assert plan.params["pendingBelowPrice"] == "59103"
    assert plan.params["pendingBelowTimeInForce"] == "GTC"


def test_filtreye_uymayan_emir_gonderilmez():
    plan = plan_otoco(rules=RULES, token="abc", attempt=1, entry=D("60000"),
                      target=D("60900"), stop=D("59400"), quantity=D("0.00005"),
                      fee_rate=D("0.001"), carried_dust=D("0"), settings=ExecutionSettings())
    assert not plan.gecerli
    assert any("Giriş" in item for item in plan.sorunlar)
    bad_order = plan_otoco(rules=RULES, token="abc", attempt=1, entry=D("60000"),
                           target=D("59000"), stop=D("59400"), quantity=D("0.002"),
                           fee_rate=D("0.001"), carried_dust=D("0"),
                           settings=ExecutionSettings())
    assert any("Fiyat sırası" in item for item in bad_order.sorunlar)


def test_yeniden_koruma_ve_cikis_planlari():
    oco = plan_oco(rules=RULES, token="abc", attempt=2, target=D("60900"), stop=D("59400"),
                   quantity=D("0.0014987"), settings=ExecutionSettings())
    assert oco.params["quantity"] == "0.00149"
    assert oco.params["listClientOrderId"].endswith("-K2L")
    exit_plan = plan_exit(rules=RULES, token="abc", attempt=3, quantity=D("0.00149"),
                          best_bid=D("59300"), settings=ExecutionSettings())
    assert exit_plan.params["price"] == "59003.5"
    assert exit_plan.params["timeInForce"] == "IOC"
    assert exit_plan.params["newClientOrderId"].endswith("-C3")
    assert sell_quantity(RULES, bought=D("0.00166"), fee_rate=D("0.001"),
                         carried_dust=D("-1")) == D("0.00165")


# --- hata sınıfları ---------------------------------------------------------------------------


def _error(status: int, code: int | None, msg: str = "") -> ExchangeError:
    return ExchangeError(status=status, code=code, msg=msg, method="POST", path="/x")


def test_hatalar_dogru_siniflanir():
    cases = [
        (_error(400, -2010, "Order would immediately match and take."),
         errors.ERR_WOULD_MATCH),
        (_error(400, -2010, "Order would trigger immediately."), errors.ERR_WOULD_TRIGGER),
        (_error(400, -2010, "Account has insufficient balance for requested action."),
         errors.ERR_BALANCE),
        (_error(400, -2010, "Duplicate order sent."), errors.ERR_DUPLICATE),
        (_error(400, -1013, "Filter failure: NOTIONAL"), errors.ERR_FILTER),
        (_error(400, -2011, "Unknown order sent."), errors.ERR_UNKNOWN_ORDER),
        (_error(400, -1021), errors.ERR_TIMESTAMP),
        (_error(401, -2015), errors.ERR_KEY),
        (_error(429, -1003), errors.ERR_RATE),
        (_error(418, -1003), errors.ERR_BAN),
        (_error(400, -1015), errors.ERR_ORDER_RATE),
        (_error(503, -1007), errors.ERR_UNKNOWN),
        (_error(0, None, "timed out"), errors.ERR_NETWORK),
        (OutcomeUnknown(method="POST", path="/x", detail="t", sent_ms=1), errors.ERR_UNKNOWN),
        (TradingRefused("x"), errors.ERR_REFUSED),
    ]
    for error, category in cases:
        info = errors.classify(error)
        assert info.kategori == category, (error, info)
        assert info.mesaj_tr
    assert errors.classify(cases[0][0]).yeniden_fiyatla
    assert errors.classify(cases[-2][0]).belirsiz
    assert errors.classify(cases[3][0]).belirsiz  # kimlik tekrarı: sorgulanır


# --- hesap akışı ----------------------------------------------------------------------------


class FakeConnection:
    """WebSocket API bağlantısı yerine: abonelik yanıtından sonra ``after``'ı verir."""

    def __init__(self, after: list[str]) -> None:
        self.sent: list[dict] = []
        self.replies: queue.Queue[str] = queue.Queue()
        self.after = after
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))
        request_id = self.sent[-1]["id"]
        self.replies.put(json.dumps({"id": request_id, "status": 200,
                                     "result": {"subscriptionId": 0}}))
        for item in self.after:
            self.replies.put(item)

    def recv(self, timeout: float | None = None) -> str:
        if self.closed:
            raise ConnectionError("kapandı")
        try:
            return self.replies.get(timeout=min(timeout or 0.2, 0.2))
        except queue.Empty:
            raise TimeoutError from None

    def close(self) -> None:
        self.closed = True


def _report(**changes: object) -> str:
    event = {"e": "executionReport", "E": 1, "s": BTC, "c": "albsat-demo-abc-G1", "S": "BUY",
             "o": "LIMIT_MAKER", "f": "GTC", "q": "0.001", "p": "60000", "P": "0",
             "g": 5, "C": "", "x": "TRADE", "X": "PARTIALLY_FILLED", "r": "NONE", "i": 9,
             "l": "0.0004", "z": "0.0004", "L": "60000", "n": "0.0000004", "N": "BTC",
             "T": 2, "t": 77, "m": True, "Z": "24", "Y": "24"}
    event.update(changes)
    return json.dumps({"subscriptionId": 0, "event": event})


def test_akis_olaylari_okunur():
    update = parse_user_message(_report())
    assert isinstance(update, OrderUpdate)
    assert update.dolum_var and update.son_dolum_miktari == D("0.0004")
    assert update.komisyon_varligi == "BTC" and update.maker
    cancel = parse_user_message(_report(c="rastgele", C="albsat-demo-abc-G1", x="CANCELED",
                                        X="CANCELED", t=-1))
    assert isinstance(cancel, OrderUpdate) and cancel.kimlik == "albsat-demo-abc-G1"
    assert not cancel.dolum_var
    listed = parse_user_message(json.dumps({"event": {
        "e": "listStatus", "s": BTC, "g": 5, "c": "OCO", "l": "ALL_DONE", "L": "ALL_DONE",
        "C": "albsat-demo-abc-L1", "T": 3, "O": [{"s": BTC, "i": 9, "c": "x"}]}}))
    assert isinstance(listed, ListUpdate) and listed.emirler == ("x",)
    balance = parse_user_message(json.dumps({"event": {
        "e": "outboundAccountPosition", "u": 4, "B": [{"a": "BTC", "f": "0.1", "l": "0.2"}]}}))
    assert isinstance(balance, BalanceUpdate) and balance.bakiyeler["BTC"] == (D("0.1"),
                                                                               D("0.2"))
    notice = parse_user_message(json.dumps({"event": {"e": "serverShutdown"}}))
    assert isinstance(notice, StreamNotice)
    assert parse_user_message("bozuk") is None


def test_hesap_akisi_imzali_abone_olur_ve_olaylari_iletir():
    events: list[object] = []
    states: list[tuple[bool, str]] = []
    connection = FakeConnection([_report()])
    stream = UserStream("wss://demo-ws-api.binance.com/ws-api/v3", key=KEY,
                        on_event=events.append, on_state=lambda *a: states.append(a),
                        connector=lambda url: connection, server_time_ms=lambda: 1_700_000_000_000)
    stream.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not events:
        time.sleep(0.02)
    stream.stop()
    assert events and isinstance(events[0], OrderUpdate)
    assert states and states[0][0] is True
    request = connection.sent[0]
    assert request["method"] == "userDataStream.subscribe.signature"
    params = dict(request["params"])
    signature = params.pop("signature")
    assert params["apiKey"] == API_KEY
    public = load_pem_public_key(PUBLIC_PEM.encode())
    public.verify(base64.b64decode(signature), signature_payload(params).encode())  # type: ignore[call-arg, union-attr]
    assert "demoAnahtar" not in repr(stream)


def test_sunucu_kapanirken_yeniden_baglanir():
    connections: list[FakeConnection] = []

    def connector(url: str) -> FakeConnection:
        replies = [json.dumps({"event": {"e": "serverShutdown"}})] if not connections else []
        connection = FakeConnection(replies)
        connections.append(connection)
        return connection

    events: list[object] = []
    stream = UserStream("wss://x", key=KEY, on_event=events.append, connector=connector,
                        server_time_ms=lambda: 1)
    stream.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and len(connections) < 2:
        time.sleep(0.05)
    stream.stop()
    assert len(connections) >= 2


# --- ayarlar ----------------------------------------------------------------------------------


def test_ayarlar_dogrulanir_ve_yaml_ile_ayni():
    defaults = ExecutionSettings()
    text = (ROOT / "config" / "default.yaml").read_text(encoding="utf-8")
    section = text.split("\nemir:\n", 1)[1].split("\n\n", 1)[0]

    def value(name: str) -> str:
        match = re.search(rf'^\s*{name}:\s*"?([A-Z_0-9.]+)"?', section, re.MULTILINE)
        assert match, name
        return match.group(1)

    assert value("stop_tipi") == defaults.stop_tipi
    assert D(value("stop_limit_ofset_yuzde")) == defaults.stop_limit_ofset_yuzde
    assert D(value("azami_kayma_yuzde")) == defaults.azami_kayma_yuzde
    assert int(value("korumasiz_azami_saniye")) == defaults.korumasiz_azami_saniye
    assert int(value("yeniden_fiyatlama_denemesi")) == defaults.yeniden_fiyatlama_denemesi
    for changes in ({"stop_tipi": "MARKET"}, {"azami_kayma_yuzde": "9"},
                    {"korumasiz_azami_saniye": 1}, {"yeniden_fiyatlama_denemesi": 9},
                    {"bilinmeyen": 1}, {"korumasiz_azami_saniye": True}):
        with pytest.raises(SettingsError):
            defaults.updated(changes)
    assert defaults.updated({"stop_tipi": STOP_LIMIT}).stop_tipi == STOP_LIMIT


# --- kurulum -----------------------------------------------------------------------------------


def _engine(root: Path) -> PaperEngine:
    engine = PaperEngine(root, symbols=(BTC,), notifier=MemoryNotifier(),
                         costs_for=lambda s: None, rules_for=lambda s: RULES)  # type: ignore[arg-type,return-value]
    engine.modes.start_session()
    return engine


def test_anahtar_yoksa_demo_secilemez_ve_nedeni_soylenir(tmp_path):
    from albsat.data.live import LiveMarket
    from albsat.data.store import KlineStore

    engine = _engine(tmp_path)
    market = LiveMarket(KlineStore(tmp_path))
    executor = build_executor(tmp_path, symbols=(BTC,), engine=engine, live_market=market,
                              notifier=engine.notifier, online=True, key_loader=lambda: None)
    assert executor.trader is None
    assert executor.not_ready_reason() == NO_KEY
    with pytest.raises(ValueError, match="demo-anahtar"):
        engine.set_mode(BTC, MODE_DEMO, source=SOURCE_UI, now=__import__(
            "albsat.core.clock", fromlist=["utc_now"]).utc_now())
    offline = build_executor(tmp_path, symbols=(BTC,), engine=_engine(tmp_path),
                             live_market=market, notifier=engine.notifier, online=False)
    assert offline.not_ready_reason() == OFFLINE


def test_anahtar_varsa_demo_adresleriyle_kurulur(tmp_path):
    from albsat.data.live import LiveMarket
    from albsat.data.store import KlineStore

    engine = _engine(tmp_path)
    executor = build_executor(tmp_path, symbols=(BTC,), engine=engine,
                              live_market=LiveMarket(KlineStore(tmp_path)),
                              notifier=engine.notifier, online=True, key_loader=lambda: KEY)
    assert executor.trader is not None
    assert executor.trader.base == "https://demo-api.binance.com"
    assert executor.stream.url == "wss://demo-ws-api.binance.com/ws-api/v3"
    assert executor.book_stream.url.startswith("wss://demo-stream.binance.com/stream?")
    assert executor.public.rest_base == "https://demo-api.binance.com/api"
    # Hazırlık bitmeden Demo Mode seçilemez.
    assert "hazırlanıyor" in (executor.not_ready_reason() or "")


def test_hesabin_cekim_bayragi_engel_sayilmaz_islem_kapaliysa_reddedilir(tmp_path):
    # Demo hesabı canWithdraw=true döndürüyor (23 Eylül 2026, Berk'in Mac'i). Bu
    # hesabın bayrağıdır, anahtarın izni değil; Demo'da para çekme yok.
    rig = build(tmp_path, bootstrap=False)
    rig.fake.can_withdraw = True
    assert rig.executor.bootstrap()
    assert rig.executor.not_ready_reason() is None
    closed = build(tmp_path / "kapali", bootstrap=False)
    closed.fake.can_trade = False
    assert not closed.executor.bootstrap()
    assert "işlem yapamıyor" in (closed.executor.not_ready_reason() or "")
    assert not closed.fake.posts()


# --- arayüz uçları ---------------------------------------------------------------------------


class _StubRunner:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def marks(self) -> dict[str, Decimal]:
        return {BTC: self.rig.fake.book[BTC][0]}

    def status(self) -> dict[str, object]:
        return {"akis_bagli": True}


class _StubMarket:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def market_state(self, sembol, periyot, rules):  # noqa: ANN001, ANN201
        from test_kagit_islem import market

        return market(self.rig.clock.now())

    def quote(self, sembol):  # noqa: ANN001, ANN201
        return None

    def bid(self, sembol):  # noqa: ANN001, ANN201
        return self.rig.fake.book[BTC][0]

    def usdttry(self) -> None:
        return None


def _client(rig: Rig) -> TestClient:
    runtime = Runtime(root=rig.root, symbols=(BTC,), periods=("15m", "1h"),
                      notifier=rig.notifier, engine=rig.engine,
                      market=_StubMarket(rig),  # type: ignore[arg-type]
                      runner=_StubRunner(rig),  # type: ignore[arg-type]
                      demo=rig.executor, telegram_note="yok")
    state = AppState(veri_dizini=rig.root, semboller=(BTC,), periyotlar=("15m", "1h"),
                     runtime=runtime)
    return TestClient(create_app(state), base_url="http://127.0.0.1")


def test_demo_uclari_durum_onizleme_emir_iptal(tmp_path, monkeypatch):
    rig = build(tmp_path)
    monkeypatch.setattr("albsat.api.demo_api.utc_now", rig.clock.now)
    client = _client(rig)
    status = client.get("/api/demo/durum").json()
    assert status["baglanti"]["hazir"] is True
    assert status["baglanti"]["ortam"].startswith("Binance Demo Mode")
    assert status["hesap"]["baslangic_usdt"] == "100.00"
    # Demo defterinin fiyatı akıştan okunur; arayüz yenilemesi borsaya istek atmaz.
    requests_before = len(rig.fake.requests)
    assert status["defter"][0]["sembol"] == BTC and status["defter"][0]["alis"]
    client.get("/api/demo/durum")
    assert len(rig.fake.requests) == requests_before
    assert client.get("/api/kagit/durum").json()["demo_hazir_degil"] is None
    body = {"sembol": BTC, "periyot": "15m", "giris": "60000", "hedef": "60900",
            "stop": "59400"}
    preview = client.get("/api/demo/on-izleme", params=body).json()
    assert preview["karar"]["izin"] is True, preview["karar"]["ozet"]
    assert [item["rol"] for item in preview["plan"]["emirler"]] == ["Giriş", "Hedef", "Stop"]
    assert not rig.fake.posts()  # ön izleme hiçbir şey göndermez
    # Yazma koruması: özel başlık yoksa reddedilir.
    assert client.post("/api/demo/emir", json=body).status_code == 403
    placed_ = client.post("/api/demo/emir", json=body, headers=YAZ).json()
    assert placed_["pozisyon"]["durum"] == "bekliyor", placed_
    position_id = placed_["pozisyon"]["id"]
    assert len(rig.fake.posts("/api/v3/orderList/otoco")) == 1
    cancelled = client.post("/api/demo/iptal", json={"id": position_id}, headers=YAZ)
    assert cancelled.status_code == 200
    rig.tick()
    assert rig.position(position_id).durum == "iptal"
    summary = client.post("/api/demo/uzlastir", json={}, headers=YAZ).json()
    assert "Demo uzlaştırma" in summary["ozet"]
    settings = client.post("/api/demo/ayarlar",
                           json={"degerler": {"korumasiz_azami_saniye": 30}}, headers=YAZ)
    assert settings.json()["degerler"]["korumasiz_azami_saniye"] == 30
    bad = client.post("/api/demo/ayarlar", json={"degerler": {"azami_kayma_yuzde": "50"}},
                      headers=YAZ)
    assert bad.status_code == 400
    csv_text = client.get("/api/demo/islemler.csv").text
    assert csv_text.lstrip("﻿").startswith("tarih_istanbul;")


def test_demo_ucundan_kapatma(tmp_path, monkeypatch):
    rig = build(tmp_path)
    monkeypatch.setattr("albsat.api.demo_api.utc_now", rig.clock.now)
    client = _client(rig)
    position = placed(rig)
    fill_entry(rig, position.id)
    rig.tick()
    assert rig.position(position.id).durum == POS_PROTECTED
    closing = client.post("/api/demo/kapat", json={"id": position.id}, headers=YAZ)
    assert closing.status_code == 200
    rig.tick(2)
    closed = rig.position(position.id)
    assert closed.durum == POS_CLOSED and closed.cikis_sebebi == "elle_kapatma"


def test_arayuzdeki_acil_durdur_demo_girisini_iptal_eder(tmp_path, monkeypatch):
    rig = build(tmp_path)
    monkeypatch.setattr("albsat.api.demo_api.utc_now", rig.clock.now)
    client = _client(rig)
    position = placed(rig)
    result = client.post("/api/kagit/acil-durdur", json={}, headers=YAZ).json()
    assert result["demo_kapatilan"] == 0
    rig.tick()
    assert rig.position(position.id).durum == "iptal"
    assert rig.engine.modes.get(BTC) == MODE_ADVICE
    assert rig.fake.order(f"albsat-demo-{position.jeton}-G1").status == "CANCELED"


def test_telegram_durum_ve_durdur_demoyu_kapsar(tmp_path):
    rig = build(tmp_path)
    position = placed(rig)
    handler = build_handler(rig.engine, marks=lambda: {}, connection=lambda: None,
                            clock=rig.clock.now, demo=rig.executor)
    text = handler("/durum", "")
    assert "Demo Mode: hazır" in text and f"#{position.id}" in text
    answer = handler("/durdur", "")
    assert "Demo" in answer
    rig.tick()
    assert rig.position(position.id).durum == "iptal"
