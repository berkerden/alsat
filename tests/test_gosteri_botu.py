"""Gösteri botu (docs/GOSTERI-BOTU.md): kural değildir, yalnızca kâğıtta çalışır.

Sınananlar: emir yalnızca Kâğıt İşlem modunda ve yalnızca kâğıt motoruna
açılır; Demo ve canlı yürütücülere hiçbir yoldan ulaşmaz; sonuçları ayrı
sayılır, canlıya geçiş kapısına ve kural performansına girmez; varsayılan
olarak kapalıdır.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
import veri_uret
from fastapi.testclient import TestClient
from test_canli_dongu import FakeHttp, FakeStream, WallClock
from test_kagit_arayuz import BASE, YAZ, _runtime
from test_kagit_islem import COSTS, RULES
from test_sizing import PAYLOAD

from albsat.api.app import AppState, create_app
from albsat.data.exchangeinfo import ExchangeInfoStore
from albsat.data.live import LiveMarket
from albsat.data.store import KlineStore
from albsat.execution.gate import rule_gates
from albsat.modes.state import MODE_DEMO, MODE_PAPER
from albsat.notify.base import MemoryNotifier
from albsat.paper.engine import PaperEngine
from albsat.paper.ledger import STATUS_PENDING
from albsat.paper.report import summarize
from albsat.paper.runner import LiveRunner
from albsat.risk.engine import SOURCE_MANUAL, SOURCE_RULE, SOURCE_SHOW
from albsat.strategy import gosteri

D = Decimal
HOUR = 3_600_000
MINUTE = 60_000


# --- saf işlevler ---------------------------------------------------------------


def _frame(open_: float, close: float) -> pd.DataFrame:
    return pd.DataFrame([{"open_time": 10 * HOUR, "open": open_, "high": max(open_, close),
                          "low": min(open_, close), "close": close}])


def test_yukselisle_kapanan_mum_sinyal_verir_dususle_kapanan_vermez():
    found = gosteri.signal(_frame(60000.0, 60100.0), step_ms=HOUR)
    assert found is not None
    assert found.close == D("60100.0")
    assert found.close_time_ms == 11 * HOUR
    assert gosteri.signal(_frame(60100.0, 60000.0), step_ms=HOUR) is None
    assert gosteri.signal(_frame(60000.0, 60000.0), step_ms=HOUR) is None
    assert gosteri.signal(pd.DataFrame(), step_ms=HOUR) is None


def test_plan_hedef_ve_stopu_esit_uzaklikta_ve_adima_yuvarlar():
    planned = gosteri.plan(D("60000"), D("0.75"), RULES)
    assert planned is not None
    assert planned.giris == D("60000.00")
    assert planned.hedef == D("60450.00")
    assert planned.stop == D("59550.00")


def test_plan_atr_kucukse_en_kucuk_uzakligi_kullanir():
    planned = gosteri.plan(D("60000"), D("0.05"), RULES)
    assert planned is not None
    assert planned.uzaklik_yuzde == gosteri.MIN_DISTANCE_PCT
    assert planned.hedef == D("60240.00")


def test_plan_atr_yoksa_emir_yok():
    assert gosteri.plan(D("60000"), None, RULES) is None


# --- canlı döngü ----------------------------------------------------------------


def _setup(tmp_path, *, yukselis: bool = True):
    """1 saatlik veri; son kapanmış mum istenen yönde kapanır."""
    frame = veri_uret.random_walk(400, seed=3, start=60000.0, step_ms=HOUR)
    last = frame.index[-1]
    open_ = float(frame.loc[last, "open"])
    close = open_ * (1.002 if yukselis else 0.998)
    frame.loc[last, "close"] = close
    frame.loc[last, "high"] = max(open_, close) * 1.0005
    frame.loc[last, "low"] = min(open_, close) * 0.9995
    KlineStore(tmp_path).write(frame, symbol="BTCUSDT", interval="1h")
    ExchangeInfoStore(tmp_path).write({"symbols": [PAYLOAD]})
    close_ms = int(frame["open_time"].max()) + HOUR
    return close_ms, D(f"{close:.2f}")


def _runner(tmp_path, clock, price: Decimal):
    notifier = MemoryNotifier()
    engine = PaperEngine(tmp_path, symbols=("BTCUSDT",), notifier=notifier,
                         costs_for=lambda symbol: COSTS, rules_for=lambda symbol: RULES)
    engine.modes.start_session()
    market = LiveMarket(KlineStore(tmp_path), clock=clock)
    runner = LiveRunner(
        tmp_path, symbols=("BTCUSDT",), periods=("1h",), engine=engine, market=market,
        http=FakeHttp(clock, str(price)), notifier=notifier,
        stream_base="wss://example.invalid/stream", ruleset_loader=lambda: None,
        stream_factory=FakeStream, clock=clock,
    )
    return runner, engine, market, notifier


def _ready(tmp_path, *, yukselis: bool = True, acik: bool = True, mod: str | None = MODE_PAPER):
    close_ms, close = _setup(tmp_path, yukselis=yukselis)
    clock = WallClock(datetime.fromtimestamp(close_ms / 1000, UTC) - timedelta(minutes=5))
    runner, engine, market, notifier = _runner(tmp_path, clock, close)
    if mod is not None:
        engine.modes.set("BTCUSDT", mod)
    if acik:
        engine.ledger.set_state(gosteri.STATE_ENABLED, "1")
    for _ in range(300):
        market.on_book("BTCUSDT", close + D("0.01"), close + D("0.02"))
        clock.advance(seconds=1)
    market.on_ticker("BTCUSDT", close, D("900000000"))
    runner._catch_up("BTCUSDT", online=True, warm=True)
    clock.now = datetime.fromtimestamp(close_ms / 1000, UTC) + timedelta(seconds=20)
    market.on_book("BTCUSDT", close + D("0.01"), close + D("0.02"))
    return runner, engine, notifier, clock, close


def test_acikken_kagit_modunda_gosteri_emri_acilir_ve_tekrarlanmaz(tmp_path):
    runner, engine, notifier, clock, close = _ready(tmp_path)
    assert runner.gosteri_step("BTCUSDT", "1h") is True, notifier.texts
    active = engine.ledger.active()
    assert len(active) == 1
    order = active[0]
    assert order.durum == STATUS_PENDING
    assert order.kaynak == SOURCE_SHOW
    assert order.kural_kimligi is None
    assert order.kural_etiketi == gosteri.LABEL
    assert order.periyot == "1h"
    # Alış limiti borsanın fiyat adımına aşağı yuvarlanır.
    assert close - D("0.01") <= D(order.giris) <= close
    assert D(order.hedef) > D(order.giris) > D(order.stop)
    assert order.azami_tutma_ms == gosteri.HOLD_CANDLES * HOUR
    # Aynı mum ikinci kez emir açmaz.
    assert runner.gosteri_step("BTCUSDT", "1h") is False
    assert len(engine.ledger.active()) == 1


def test_varsayilan_olarak_kapalidir(tmp_path):
    runner, engine, _, _, _ = _ready(tmp_path, acik=False)
    assert runner.gosteri_enabled() is False
    assert runner.gosteri_step("BTCUSDT", "1h") is False
    assert engine.ledger.active() == ()


def test_sadece_oneri_modunda_emir_acmaz(tmp_path):
    runner, engine, _, _, _ = _ready(tmp_path, mod=None)
    assert runner.gosteri_step("BTCUSDT", "1h") is False
    assert engine.ledger.active() == ()


def test_dususle_kapanan_mumda_emir_acmaz(tmp_path):
    runner, engine, _, _, _ = _ready(tmp_path, yukselis=False)
    assert runner.gosteri_step("BTCUSDT", "1h") is False
    assert engine.ledger.active() == ()


def test_baska_periyotta_calismaz(tmp_path):
    runner, engine, _, _, _ = _ready(tmp_path)
    assert runner.gosteri_step("BTCUSDT", "15m") is False
    assert engine.ledger.active() == ()


def test_eski_mum_emre_donusmez(tmp_path):
    runner, engine, _, clock, _ = _ready(tmp_path)
    clock.advance(minutes=10)
    assert runner.gosteri_step("BTCUSDT", "1h") is False
    assert engine.ledger.active() == ()


class _Patlar:
    """Demo ya da canlı yürütücünün yerine: çağrılırsa test düşer."""

    def __getattr__(self, name):
        raise AssertionError(f"gösteri botu yürütücüye dokundu: {name}")


def test_demo_ve_canli_yurutuculere_hic_ulasmaz(tmp_path):
    runner, engine, _, _, _ = _ready(tmp_path)
    runner.demo = _Patlar()
    runner.live = _Patlar()
    assert runner.gosteri_step("BTCUSDT", "1h") is True
    # Coin Demo Mode'dayken gösteri botu hiçbir şey yapmaz (Demo'ya emir gitmez).
    engine.ledger.set_state(gosteri.STATE_LAST_CANDLE + "BTCUSDT", "0")
    engine.kill_switch(source="test", now=runner.clock(), close_positions=False, marks={})
    engine.modes.set("BTCUSDT", MODE_DEMO)
    assert runner.gosteri_step("BTCUSDT", "1h") is False


def test_donem_mumu_gelince_dongu_gosteri_adimini_calistirir(tmp_path):
    runner, engine, _, _, _ = _ready(tmp_path)
    frame = KlineStore(tmp_path).read("BTCUSDT", "1h")
    row = frame.iloc[-1]
    ham = (int(row["open_time"]), str(row["open"]), str(row["high"]), str(row["low"]),
           str(row["close"]), "1", int(row["open_time"]) + HOUR - 1, "1", 1, "0", "0", "0")
    runner._on_period("BTCUSDT", "1h", ham)
    assert [item.kaynak for item in engine.ledger.active()] == [SOURCE_SHOW]


# --- ayrı sayım: kapı ve performans ------------------------------------------------


def test_gosteri_islemleri_kapiya_ve_kural_performansina_girmez(tmp_path):
    runner, engine, _, clock, close = _ready(tmp_path)
    assert runner.gosteri_step("BTCUSDT", "1h") is True
    order = engine.ledger.active()[0]
    # Giriş dolsun, sonra hedef görülsün.
    start = (int(clock.now.timestamp() * 1000) // MINUTE + 1) * MINUTE
    giris, hedef = D(order.giris), D(order.hedef)
    from albsat.paper.fills import Candle

    for index, (o, h, low, c) in enumerate([
        (giris, giris, giris - 5, giris - 2),
        (giris - 2, hedef + 5, giris - 3, hedef + 2),
    ]):
        clock.advance(minutes=1)
        engine.process_candle(
            "BTCUSDT",
            Candle(start + index * MINUTE, start + (index + 1) * MINUTE, o, h, low, c),
            online=True, now=clock.now + timedelta(minutes=1),
        )
    closed = engine.ledger.all_closed()
    assert len(closed) == 1 and closed[0].kaynak == SOURCE_SHOW
    now = clock.now + timedelta(days=30)
    assert engine.rule_checks(now) == []
    assert rule_gates(ruleset=None, paper_orders=closed, checks=engine.rule_checks(now),
                      disabled=engine.disabled_rules(), now=now) == []
    assert summarize(closed, SOURCE_SHOW).islem == 1
    assert summarize(closed, SOURCE_RULE).islem == 0
    assert summarize(closed, SOURCE_MANUAL).islem == 0


# --- arayüz uçları --------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    runtime = _runtime(tmp_path)
    state = AppState(veri_dizini=tmp_path, semboller=("BTCUSDT",),
                     periyotlar=("15m", "1h"), runtime=runtime)
    return TestClient(create_app(state), base_url=BASE), runtime


def test_arayuzden_acilir_kapanir_ve_denetime_yazilir(client):
    http, runtime = client
    durum = http.get("/api/kagit/durum").json()
    assert durum["gosteri"]["acik"] is False
    assert "kural değildir" in durum["gosteri"]["aciklama"]
    assert durum["ozet"]["gosteri"]["islem"] == 0
    yanit = http.post("/api/kagit/gosteri", json={"acik": True}, headers=YAZ)
    assert yanit.status_code == 200 and yanit.json()["gosteri"]["acik"] is True
    assert http.get("/api/kagit/durum").json()["gosteri"]["acik"] is True
    assert any("Gösteri botu açıldı" in item.ozet for item in runtime.engine.audit.recent(5))
    assert any("Gösteri botu açıldı" in text for text in runtime.notifier.texts)
    assert http.post("/api/kagit/gosteri", json={"acik": False}, headers=YAZ).json()[
        "gosteri"]["acik"] is False


def test_gosteri_ucu_yerel_korumadan_gecer(client):
    http, runtime = client
    assert http.post("/api/kagit/gosteri", json={"acik": True}).status_code in (400, 403)
    assert http.post("/api/kagit/gosteri", json={"acik": True, "fazla": 1},
                     headers=YAZ).status_code == 422
    assert runtime.engine.ledger.get_state(gosteri.STATE_ENABLED) != "1"
