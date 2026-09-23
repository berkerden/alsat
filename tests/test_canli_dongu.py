"""Canlı döngü testleri: istek bütçesi, akış ayrıştırma, yeniden bağlanma,
açılış uzlaştırması ve sinyalden kâğıt emrine giden yol.

Binance'e bağlanılmaz. Borsa yerine sahte bir istemci, WebSocket yerine
sahte bir bağlantı kullanılır; saat parametre olarak verilir.
"""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import veri_uret
from test_kagit_islem import COSTS, RULES
from test_kural_deposu import rule
from test_oneri_motoru import _her_zaman_dogru_ozellik, bolum, run_summary
from test_sizing import PAYLOAD

from albsat.core.awake import SleepGuard, on_battery
from albsat.core.clock import to_ms
from albsat.data.exchangeinfo import ExchangeInfoStore
from albsat.data.live import LiveMarket
from albsat.data.store import KlineStore
from albsat.exchange import http as http_module
from albsat.exchange.http import HttpError, PublicHttp
from albsat.exchange.market_stream import (
    BookEvent,
    KlineEvent,
    MarketStream,
    StreamStatus,
    TickerEvent,
    parse_message,
    stream_names,
    stream_url,
)
from albsat.exchange.ratelimit import BudgetExceeded, RequestBudget
from albsat.modes.state import MODE_ADVICE, MODE_PAPER
from albsat.notify.base import MemoryNotifier
from albsat.paper.engine import PaperEngine
from albsat.paper.ledger import STATUS_CANCELLED, STATUS_PENDING
from albsat.paper.runner import STATE_HEARTBEAT, LiveRunner
from albsat.risk.engine import SOURCE_MANUAL, OrderIntent
from albsat.risk.market import MarketState
from albsat.strategy.rules import RuleSet

D = Decimal
MINUTE = 60_000


# --- istek bütçesi -------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_yerel_tavan_istegi_gondermeden_durdurur():
    clock = FakeClock()
    budget = RequestBudget(local_cap=10, clock=clock)
    budget.reserve(4, "a")
    budget.reserve(4, "b")
    with pytest.raises(BudgetExceeded):
        budget.reserve(4, "c")
    assert budget.status().reddedilen == 1
    clock.now += 61
    budget.reserve(4, "d")  # dakika geçince yine izin var


def test_429_sonrasi_retry_after_suresince_istek_yok():
    clock = FakeClock()
    budget = RequestBudget(clock=clock)
    budget.rate_limited(30)
    with pytest.raises(BudgetExceeded) as error:
        budget.reserve(1, "/api/v3/klines")
    assert "429" in str(error.value)
    clock.now += 31
    budget.reserve(1, "/api/v3/klines")


def test_418_engeli_bildirir_ve_istekleri_keser():
    clock = FakeClock()
    messages: list[str] = []
    budget = RequestBudget(clock=clock, on_ban=messages.append)
    budget.banned(120)
    assert messages and "418" in messages[0]
    assert budget.status().engelli
    with pytest.raises(BudgetExceeded):
        budget.reserve(1, "x")


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_istemci_429da_bekler_ve_sonraki_istegi_gondermez(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many", {"Retry-After": "45"}, io.BytesIO(b"{}")
        )

    monkeypatch.setattr(http_module.urllib.request, "urlopen", fake_urlopen)
    budget = RequestBudget()
    client = PublicHttp(budget=budget, retries=3, backoff=0)
    with pytest.raises(HttpError):
        client.server_time()
    assert len(calls) == 1  # 429'da yeniden deneme yok
    with pytest.raises(BudgetExceeded):
        client.server_time()
    assert len(calls) == 1  # bütçe isteği göndermeden durdurdu
    assert budget.status().bekleme_saniye > 40


def test_istemci_kullanilan_agirligi_okur(monkeypatch):
    def fake_urlopen(request, timeout):
        return _Response(b'{"serverTime": 1}', {"X-MBX-USED-WEIGHT-1M": "37"})

    monkeypatch.setattr(http_module.urllib.request, "urlopen", fake_urlopen)
    budget = RequestBudget()
    PublicHttp(budget=budget).server_time()
    status = budget.status()
    assert status.borsa_1dk == 37
    assert status.yerel_1dk == 1


# --- akış ayrıştırma -------------------------------------------------------------


def kline_message(symbol="BTCUSDT", interval="1m", start=0, closed=True, o="100",
                  h="101", low="99", c="100.5"):
    step = {"1m": MINUTE, "15m": 15 * MINUTE, "1h": 60 * MINUTE}[interval]
    return json.dumps({
        "stream": f"{symbol.lower()}@kline_{interval}",
        "data": {
            "e": "kline", "E": start + 1, "s": symbol,
            "k": {"t": start, "T": start + step - 1, "s": symbol, "i": interval,
                  "f": 1, "L": 2, "o": o, "c": c, "h": h, "l": low, "v": "10",
                  "n": 5, "x": closed, "q": "1000", "V": "5", "Q": "500", "B": "0"},
        },
    })


def test_kline_mesaji_ayristirilir():
    event = parse_message(kline_message(start=1_740_000_000_000, c="100.25"))
    assert isinstance(event, KlineEvent)
    assert event.sembol == "BTCUSDT" and event.periyot == "1m" and event.kapandi
    assert event.mum.close == D("100.25")
    assert event.mum.close_time_ms == 1_740_000_000_000 + MINUTE


def test_book_ve_ticker_mesajlari_ayristirilir():
    book = parse_message(json.dumps({"stream": "btcusdt@bookTicker", "data": {
        "u": 1, "s": "BTCUSDT", "b": "60000.10", "B": "1", "a": "60000.20", "A": "2"}}))
    assert book == BookEvent("BTCUSDT", D("60000.10"), D("60000.20"))
    ticker = parse_message(json.dumps({"stream": "usdttry@miniTicker", "data": {
        "e": "24hrMiniTicker", "E": 1, "s": "USDTTRY", "c": "41.50", "o": "41",
        "h": "42", "l": "40", "v": "1", "q": "123456.7"}}))
    assert ticker == TickerEvent("USDTTRY", D("41.50"), D("123456.7"))


def test_bozuk_mesaj_akisi_durdurmaz():
    assert parse_message("not json") is None
    assert parse_message(json.dumps({"stream": "x", "data": {"e": "kline"}})) is None
    assert parse_message(json.dumps([1, 2])) is None


def test_akis_adresi_kucuk_harf_ve_birlesik():
    names = stream_names(["BTCUSDT"], ["1m", "15m"], extra_tickers=["USDTTRY"])
    assert names == ["btcusdt@kline_1m", "btcusdt@kline_15m", "btcusdt@bookTicker",
                     "btcusdt@miniTicker", "usdttry@miniTicker"]
    url = stream_url("wss://stream.binance.com/stream", names)
    assert url.startswith("wss://stream.binance.com/stream?streams=btcusdt@kline_1m/")


class FakeConnection:
    def __init__(self, messages, *, then_fail=True):
        self.messages = list(messages)
        self.then_fail = then_fail
        self.closed = False

    def recv(self, timeout=None):
        if self.closed:
            raise ConnectionError("kapalı")
        if self.messages:
            return self.messages.pop(0)
        if self.then_fail:
            raise ConnectionError("koptu")
        time.sleep(0.01)
        raise TimeoutError

    def close(self):
        self.closed = True


def test_akis_koptugunda_yeniden_baglanir():
    connections = [
        FakeConnection([kline_message(start=0)]),
        FakeConnection([kline_message(start=MINUTE)], then_fail=False),
    ]
    events = []
    states = []
    got_two = threading.Event()

    def on_event(event):
        events.append(event)
        if len(events) == 2:
            got_two.set()

    def connector(url):
        if not connections:
            raise ConnectionError("bitti")
        return connections.pop(0)

    stream = MarketStream("wss://x/stream?streams=a", on_event=on_event,
                          on_state=lambda ok, why: states.append(ok), connector=connector)
    # İlk yeniden bağlanma beklemesini kısalt.
    stream._wait_before_attempt = lambda failures: not stream._stop.is_set()
    stream.start()
    assert got_two.wait(3)
    stream.stop()
    assert [event.mum.open_time_ms for event in events] == [0, MINUTE]
    assert states[:3] == [True, False, True]
    assert stream.status().yeniden_baglanma == 1


# --- canlı piyasa ölçümleri -----------------------------------------------------------


class WallClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def test_spread_normali_yeterli_gozlem_olmadan_hesaplanmaz(tmp_path):
    clock = WallClock(datetime(2026, 9, 21, 12, tzinfo=UTC))
    market = LiveMarket(KlineStore(tmp_path), clock=clock)
    for _ in range(299):
        market.on_book("BTCUSDT", D("60000"), D("60000.01"))
        clock.advance(seconds=1)
    assert market.spread_median("BTCUSDT") is None
    state = market.market_state("BTCUSDT", "15m", None)
    assert state.spread_medyan_yuzde is None
    assert any("gözlem" in note for note in state.notlar)
    market.on_book("BTCUSDT", D("60000"), D("60000.01"))
    assert market.spread_median("BTCUSDT") is not None


def test_spread_saniyede_bir_orneklenir(tmp_path):
    clock = WallClock(datetime(2026, 9, 21, 12, tzinfo=UTC))
    market = LiveMarket(KlineStore(tmp_path), clock=clock)
    for _ in range(50):
        market.on_book("BTCUSDT", D("60000"), D("60000.01"))
    assert market.spread_samples("BTCUSDT") == 1


def test_btc_hareketi_son_60_dakikadan(tmp_path):
    from albsat.paper.fills import Candle

    market = LiveMarket(KlineStore(tmp_path))
    for index in range(60):
        start = index * MINUTE
        high = D("60600") if index == 30 else D("60010")
        market.on_minute("BTCUSDT", Candle(start, start + MINUTE, D("60000"), high,
                                           D("59990"), D("60000")))
    assert market.btc_move_pct() == D("1")


# --- açılış uzlaştırması ve sinyal yolu -----------------------------------------------


class FakeHttp:
    """Borsa yerine: 1m mumlar sabit fiyat etrafında, sayılan istekler."""

    def __init__(self, clock: WallClock, price: str = "60100") -> None:
        self.clock = clock
        self.price = D(price)
        self.budget = RequestBudget()
        self.requests: list[tuple[str, str]] = []

    def klines(self, *, symbol, interval, start_time=None, end_time=None, limit=1000):
        self.requests.append((symbol, interval))
        self.budget.reserve(2, "/api/v3/klines")
        step = {"1m": MINUTE, "15m": 15 * MINUTE, "1h": 60 * MINUTE}[interval]
        now_ms = to_ms(self.clock())
        cursor = (start_time // step) * step
        end = min(end_time or now_ms, now_ms)
        rows = []
        while cursor <= end and len(rows) < limit:
            p = str(self.price)
            rows.append([cursor, p, p, p, p, "1", cursor + step - 1, "1", 1, "0", "0", "0"])
            cursor += step
        return rows

    def server_time(self):
        return to_ms(self.clock())

    def exchange_info(self, symbols=None):
        return {"symbols": [PAYLOAD]}

    def book_tickers(self, symbols):
        return []

    def tickers_24h(self, symbols):
        return []


class FakeStream:
    def __init__(self, url, *, on_event, on_state):
        self.url = url
        self.connected = True
        self.reconnects = 0

    def start(self):
        pass

    def stop(self):
        pass

    def reconnect(self):
        self.reconnects += 1

    def status(self):
        return StreamStatus(self.connected, None, 0.5, 0, None, 10)


def make_runner(tmp_path, clock, *, ruleset=None, price="60100", sleep_guard=None):
    notifier = MemoryNotifier()
    engine = PaperEngine(
        tmp_path,
        symbols=("BTCUSDT",),
        notifier=notifier,
        costs_for=lambda symbol: COSTS,
        rules_for=lambda symbol: RULES,
    )
    engine.modes.start_session()
    market = LiveMarket(KlineStore(tmp_path), clock=clock)
    http = FakeHttp(clock, price)
    runner = LiveRunner(
        tmp_path,
        symbols=("BTCUSDT",),
        periods=("15m",),
        engine=engine,
        market=market,
        http=http,
        notifier=notifier,
        stream_base="wss://example.invalid/stream",
        ruleset_loader=lambda: ruleset,
        stream_factory=FakeStream,
        clock=clock,
        sleep_guard=sleep_guard,
    )
    return runner, engine, market, http, notifier


def healthy_market(market: LiveMarket, clock: WallClock, *, bid: str, ask: str) -> None:
    for _ in range(300):
        market.on_book("BTCUSDT", D(bid), D(ask))
        clock.advance(seconds=1)
    market.on_ticker("BTCUSDT", D(bid), D("900000000"))


def test_acilista_kacirilan_mumlar_islenir_ve_suresi_dolan_iptal_edilir(tmp_path):
    clock = WallClock(datetime(2026, 9, 21, 12, 0, 30, tzinfo=UTC))
    runner, engine, market, http, notifier = make_runner(tmp_path, clock)
    engine.modes.set("BTCUSDT", MODE_PAPER)
    state = MarketState(
        sembol="BTCUSDT", son_veri_utc=clock.now, son_fiyat=D("60100"),
        atr_yuzde=D("0.5"), atr_medyan_yuzde=D("0.4"), spread_yuzde=D("0.0002"),
        spread_medyan_yuzde=D("0.0002"), hacim_24s_usdt=D("900000000"),
        btc_60dk_degisim_yuzde=D("0.3"), kurallar=RULES,
    )
    intent = OrderIntent(sembol="BTCUSDT", periyot="15m", giris=D("60000"),
                         hedef=D("60900"), stop=D("59400"), kaynak=SOURCE_MANUAL)
    _, order = engine.place(intent, market=state, now=clock.now)
    assert order is not None
    engine.ledger.set_state(STATE_HEARTBEAT, clock.now.isoformat())

    # Uygulama 40 dakika kapalı kaldı; fiyat girişe hiç inmedi.
    clock.advance(minutes=40)
    runner.bootstrap()
    after = engine.ledger.get(order.id)
    assert after.durum == STATUS_CANCELLED
    assert "uygulama kapalıyken" in after.iptal_sebebi
    assert any("çevrimdışıydı" in text for text in notifier.texts)
    # Kaçırılan 40 mum işlendi; son işlenen mum şimdiden bir dakika önce başlıyor.
    assert engine.last_candle_ms("BTCUSDT") == (to_ms(clock.now) // MINUTE - 1) * MINUTE
    assert runner.health.saat_farki_ms is not None
    assert ExchangeInfoStore(tmp_path).exists()


def test_akista_bosluk_varsa_once_rest_ile_doldurulur(tmp_path):
    clock = WallClock(datetime(2026, 9, 21, 12, 0, 5, tzinfo=UTC))
    runner, engine, market, http, _ = make_runner(tmp_path, clock)
    runner.bootstrap()
    last = engine.last_candle_ms("BTCUSDT")
    clock.advance(minutes=5)
    gap_start = last + 5 * MINUTE
    http.requests.clear()
    runner.handle(parse_message(kline_message(start=gap_start, o="60100", h="60100",
                                              low="60100", c="60100")))
    assert ("BTCUSDT", "1m") in http.requests
    assert engine.last_candle_ms("BTCUSDT") == gap_start


def test_uyku_algilaninca_uzlastirma_yapilir(tmp_path):
    clock = WallClock(datetime(2026, 9, 21, 12, 0, 5, tzinfo=UTC))
    mono = FakeClock(100.0)
    runner, engine, market, http, notifier = make_runner(tmp_path, clock)
    runner.monotonic = mono
    runner._last_mono = mono()
    runner.bootstrap()
    # Duvar saati 20 dakika ilerledi, tekdüze saat 1 saniye: Mac uyudu.
    clock.advance(minutes=20)
    mono.now += 1
    runner.tick()
    assert runner.health.uyku_sayisi == 1
    assert runner.stream.reconnects == 1
    assert any("uykudaydı" in text for text in notifier.texts)


def _signal_setup(tmp_path):
    frame = veri_uret.random_walk(900, seed=5, start=60000.0)
    KlineStore(tmp_path).write(frame, symbol="BTCUSDT", interval="15m")
    ExchangeInfoStore(tmp_path).write({"symbols": [PAYLOAD]})
    last_close_ms = int(frame["open_time"].max()) + 15 * MINUTE
    ruleset = RuleSet(
        kosu=run_summary(),
        kurallar=(rule(ozellikler=(_her_zaman_dogru_ozellik(),), etiket="Her zaman doğru",
                       pencere_mum=3),),
        incelenen_adaylar=(),
        bolumler=(bolum(),),
    )
    close = D(f"{float(frame['close'].iloc[-1]):.2f}")
    return ruleset, last_close_ms, close


def test_sinyal_bildirim_olur_ve_kagit_modunda_emir_acilir(tmp_path):
    ruleset, last_close_ms, close = _signal_setup(tmp_path)
    start = datetime.fromtimestamp(last_close_ms / 1000, UTC) - timedelta(minutes=5)
    clock = WallClock(start)
    runner, engine, market, http, notifier = make_runner(
        tmp_path, clock, ruleset=ruleset, price=str(close)
    )
    engine.modes.set("BTCUSDT", MODE_PAPER)
    healthy_market(market, clock, bid=str(close - D("0.01")), ask=str(close + D("0.01")))
    # BTC hareketi için son 60 dakika.
    runner._catch_up("BTCUSDT", online=True, warm=True)
    clock.now = datetime.fromtimestamp(last_close_ms / 1000, UTC) + timedelta(seconds=20)
    market.on_book("BTCUSDT", close - D("0.01"), close + D("0.01"))

    cards = runner.evaluate_signals("BTCUSDT", "15m")
    assert len(cards) == 1
    assert any("SİNYAL" in text for text in notifier.texts)
    active = engine.ledger.active()
    assert len(active) == 1, [text for text in notifier.texts]
    order = active[0]
    assert order.durum == STATUS_PENDING
    assert order.kural_kimligi == cards[0].kural_kimligi
    assert order.azami_tutma_ms == 3 * 15 * MINUTE
    # Aynı sinyal ikinci kez emir açmaz.
    assert runner.evaluate_signals("BTCUSDT", "15m") == []
    assert len(engine.ledger.active()) == 1


def test_sadece_oneri_modunda_sinyal_emir_acmaz(tmp_path):
    ruleset, last_close_ms, close = _signal_setup(tmp_path)
    clock = WallClock(datetime.fromtimestamp(last_close_ms / 1000, UTC) + timedelta(seconds=20))
    runner, engine, market, http, notifier = make_runner(tmp_path, clock, ruleset=ruleset)
    assert engine.modes.get("BTCUSDT") == MODE_ADVICE
    cards = runner.evaluate_signals("BTCUSDT", "15m")
    assert len(cards) == 1
    assert engine.ledger.active() == ()


def test_eski_sinyal_mumu_kagit_emre_donusmez(tmp_path):
    ruleset, last_close_ms, close = _signal_setup(tmp_path)
    clock = WallClock(datetime.fromtimestamp(last_close_ms / 1000, UTC) + timedelta(minutes=10))
    runner, engine, market, http, notifier = make_runner(tmp_path, clock, ruleset=ruleset)
    engine.modes.set("BTCUSDT", MODE_PAPER)
    runner.evaluate_signals("BTCUSDT", "15m")
    assert engine.ledger.active() == ()


def test_giris_en_iyi_satisin_ustundeyse_alis_fiyatina_yazilir(tmp_path):
    ruleset, last_close_ms, close = _signal_setup(tmp_path)
    start = datetime.fromtimestamp(last_close_ms / 1000, UTC) - timedelta(minutes=5)
    clock = WallClock(start)
    runner, engine, market, http, notifier = make_runner(
        tmp_path, clock, ruleset=ruleset, price=str(close)
    )
    engine.modes.set("BTCUSDT", MODE_PAPER)
    bid, ask = close - D("5.00"), close - D("4.99")
    healthy_market(market, clock, bid=str(bid), ask=str(ask))
    runner._catch_up("BTCUSDT", online=True, warm=True)
    clock.now = datetime.fromtimestamp(last_close_ms / 1000, UTC) + timedelta(seconds=20)
    market.on_book("BTCUSDT", bid, ask)
    runner.evaluate_signals("BTCUSDT", "15m")
    active = engine.ledger.active()
    assert len(active) == 1, notifier.texts
    assert D(active[0].giris) == bid
    assert active[0].yeniden_fiyatlama == 1


# --- uyku engeli (SPEC §6) --------------------------------------------------


class FakeCaffeinate:
    def __init__(self):
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        return 0


def _guard(*, platform="darwin", power="Now drawing from 'AC Power'\n"):
    spawned = []

    def spawn(args):
        process = FakeCaffeinate()
        spawned.append((list(args), process))
        return process

    guard = SleepGuard(platform=platform, which=lambda name: "/usr/bin/" + name,
                       spawn=spawn, power=lambda: power, pid=4321)
    return guard, spawned


def test_uyku_engeli_kagit_islemde_caffeinate_acar_ve_kapatir():
    guard, spawned = _guard()
    assert not guard.status().etkin
    guard.want(True)
    guard.want(True)
    # Tek süreç; uygulama kapanınca caffeinate de kapansın diye -w <pid>.
    assert [args for args, _ in spawned] == [["caffeinate", "-i", "-w", "4321"]]
    assert guard.status().etkin
    assert "engelleniyor" in guard.status().aciklama
    guard.want(False)
    assert spawned[0][1].terminated
    assert not guard.status().etkin


def test_uyku_engeli_caffeinate_olurse_yeniden_acar():
    guard, spawned = _guard()
    guard.want(True)
    spawned[0][1].alive = False
    guard.want(True)
    assert len(spawned) == 2


def test_uyku_engeli_pilde_uyarir():
    guard, _ = _guard(power="Now drawing from 'Battery Power'\n -InternalBattery-0 80%")
    guard.want(True)
    status = guard.status()
    assert status.pilde is True
    assert "pille" in status.aciklama and "kapak" in status.aciklama
    assert on_battery("Now drawing from 'AC Power'") is False
    assert on_battery("") is None


def test_uyku_engeli_macos_disinda_hicbir_sey_yapmaz():
    guard, spawned = _guard(platform="linux")
    guard.want(True)
    assert spawned == []
    assert not guard.status().destekleniyor
    assert "macOS" in guard.status().aciklama


def test_canli_dongu_uyku_engelini_moda_gore_yonetir(tmp_path):
    clock = WallClock(datetime(2026, 9, 21, 12, 0, 5, tzinfo=UTC))
    guard, spawned = _guard()
    runner, engine, _, _, _ = make_runner(tmp_path, clock, sleep_guard=guard)
    runner.tick()
    assert spawned == []
    engine.modes.set("BTCUSDT", MODE_PAPER)
    runner._last_guard = float("-inf")
    runner.tick()
    assert len(spawned) == 1 and guard.active
    assert runner.status()["uyku_engeli"]["etkin"] is True
    engine.modes.set("BTCUSDT", MODE_ADVICE)
    runner._last_guard = float("-inf")
    runner.tick()
    assert spawned[0][1].terminated and not guard.active
    engine.modes.set("BTCUSDT", MODE_PAPER)
    runner._last_guard = float("-inf")
    runner.tick()
    runner.stop()
    assert spawned[1][1].terminated
