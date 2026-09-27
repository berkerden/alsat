"""Günlük trend testi (docs/TREND-ONKAYIT.md).

Üç şeyi gösterir:

* Kurallar nedenseldir ve kaynaklarındaki tanımla birebir çalışır.
* Sağlamlık sınaması doğru kalibre: kuralın al-ve-tut'a gerçek üstünlüğü
  olmayan veride yanlış alarm vermez, bilerek eğilim konmuş veride kural geçer.
* Komut satırı aracı en fazla birkaç istek gönderir, bozuk ya da eksik
  veride sınamayı başlatmaz ve bir kez çalışır.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from albsat.cli import trend as trend_cli
from albsat.data.klines import KLINE_COLUMNS
from albsat.data.store import KlineStore
from albsat.research import trend
from albsat.research.trend_report import render

DAY_MS = 86_400_000
COST = 0.0014


def daily_frame(returns: np.ndarray, *, end: dt.date | None = None, seed: int = 5) -> pd.DataFrame:
    """Getirilerden günlük mum tablosu; son mum ``end`` gününde (varsayılan dün)."""
    generator = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + returns)
    open_ = np.concatenate(([100.0], close[:-1]))
    wick = np.abs(generator.normal(0.0, 0.01, returns.size))
    end = end or (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1))
    last = int(dt.datetime(end.year, end.month, end.day, tzinfo=dt.UTC).timestamp() * 1000)
    times = last - (returns.size - 1 - np.arange(returns.size)) * DAY_MS
    volume = np.full(returns.size, 1000.0)
    return pd.DataFrame(
        {
            "open_time": times.astype("int64"),
            "open": open_,
            "high": np.maximum(open_, close) * (1.0 + wick),
            "low": np.minimum(open_, close) * (1.0 - wick),
            "close": close,
            "volume": volume,
            "quote_volume": volume * close,
            "trades": np.full(returns.size, 10, dtype="int64"),
            "taker_buy_base": volume / 2,
            "taker_buy_quote": volume * close / 2,
            "is_closed": True,
        }
    )


def no_trend(count: int, seed: int) -> np.ndarray:
    """Eğilimsiz: bağımsız, kalın kuyruklu günlük getiriler (hafif pozitif sürüklenme)."""
    generator = np.random.default_rng(seed)
    return 0.001 + 0.035 * generator.standard_t(3, count) / np.sqrt(3.0)


def regimes(count: int, seed: int, *, drift: float = 0.004) -> np.ndarray:
    """Bilerek eğilim konmuş: aylarca süren yükseliş ve düşüş dönemleri."""
    generator = np.random.default_rng(seed)
    out = np.empty(count)
    bull = True
    for index in range(count):
        if generator.random() < (1 / 200 if bull else 1 / 120):
            bull = not bull
        noise = generator.standard_t(4) / np.sqrt(2.0)
        out[index] = (drift + 0.03 * noise) if bull else (-drift + 0.04 * noise)
    return out


# --- kurallar ----------------------------------------------------------------------


@pytest.mark.parametrize("rule", trend.RULES, ids=lambda rule: rule.key)
def test_kural_gelecege_bakmaz(rule):
    frame = daily_frame(regimes(1_200, 3))
    before = rule.positions(frame)
    cut = 700
    spoiled = frame.copy()
    later = spoiled.index >= cut + 1
    for column in ("open", "high", "low", "close"):
        spoiled.loc[later, column] = spoiled.loc[later, column] * 7.0
    after = rule.positions(spoiled)
    assert np.array_equal(before[: cut + 1], after[: cut + 1])


@pytest.mark.parametrize("rule", trend.RULES, ids=lambda rule: rule.key)
def test_isinma_bitmeden_karar_yok(rule):
    frame = daily_frame(np.full(600, 0.01))  # hep yükselen fiyat
    positions = rule.positions(frame)
    assert not positions[: rule.warmup].any()
    assert positions[max(rule.warmup, 400):].all(), "hep yükselen fiyatta elde tutmalı"


def test_200_gunluk_ortalama_tanimi():
    returns = np.concatenate((np.full(300, 0.002), np.full(100, -0.01)))
    frame = daily_frame(returns)
    close = frame["close"]
    expected = (close > close.rolling(200).mean()).to_numpy().copy()
    expected[:199] = False
    assert np.array_equal(trend.RULES[0].positions(frame), expected)


def test_kirilim_kurali_onceki_gunlerin_kanalini_kullanir():
    close = np.full(120, 100.0)
    close[60] = 111.0            # 55 günlük tepeyi aşar: al
    close[61:70] = 112.0
    close[70] = 90.0             # 20 günlük dibin altına iner: sat
    close[71:] = 90.0
    frame = pd.DataFrame({
        "open_time": np.arange(120) * DAY_MS,
        "open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
    })
    positions = trend.RULES[2].positions(frame)
    assert not positions[:60].any()
    assert positions[60:70].all()
    assert not positions[70:].any()


def test_momentum_bir_yil_onceki_kapanisla_kiyaslar():
    returns = np.concatenate((np.full(400, 0.001), np.full(300, -0.002)))
    frame = daily_frame(returns)
    close = frame["close"].to_numpy()
    positions = trend.RULES[4].positions(frame)
    for index in (365, 450, 699):
        assert positions[index] == (close[index] > close[index - 365])


# --- getiri ve maliyet -----------------------------------------------------------


def test_al_ve_tut_giriste_ve_cikista_maliyet_oder():
    returns = np.array([0.1, -0.05, 0.02])
    net = trend.daily_net_returns(np.ones(3, dtype=bool), returns, COST)
    expected = (1 - COST) * 1.1 * 0.95 * 1.02 * (1 - COST)
    assert trend.equity_curve(net)[-1] == pytest.approx(expected)


def test_nakitte_getiri_sifir_her_degisimde_maliyet():
    returns = np.array([0.1, 0.1, 0.1, 0.1])
    positions = np.array([True, False, True, False])
    net = trend.daily_net_returns(positions, returns, COST)
    expected = (1 - COST) * 1.1 * (1 - COST) * (1 - COST) * 1.1 * (1 - COST)
    assert trend.equity_curve(net)[-1] == pytest.approx(expected)
    perf = trend.measure(positions, returns, COST)
    assert perf.entries == 2 and perf.exposure_pct == 50.0


def test_en_derin_dususler_ust_uste_binmez():
    equity = np.array([1.0, 0.5, 1.2, 1.1, 1.3, 0.65, 0.9])
    found = trend.largest_drawdowns(equity, count=3)
    assert [(item.peak, item.trough) for item in found] == [(0, 1), (4, 5), (2, 3)]
    assert found[0].depth_pct == pytest.approx(-50.0)


# --- blok bootstrap ---------------------------------------------------------------


def test_duragan_bootstrap_parcalari_ortalama_blok_uzunlugunda():
    index = trend.stationary_indices(2_000, 200, 20, np.random.default_rng(3))
    assert index.shape == (200, 2_000)
    assert index.min() >= 0 and index.max() < 2_000
    continues = index[:, 1:] == (index[:, :-1] + 1) % 2_000
    # Yeni parça olasılığı 1/20; rastgele başlangıç bazen tesadüfen devamla çakışır.
    assert 1 - continues.mean() == pytest.approx(1 / 20, abs=0.005)


def test_ayni_getiri_farki_sifir_ve_ustunluk_yok():
    returns = np.random.default_rng(2).normal(0.001, 0.03, 900)
    [test] = trend.difference_tests([returns], returns, resamples=200, block=20, seed=1)
    assert test.difference == 0.0 and test.low == 0.0 and test.high == 0.0
    assert test.p_value == 1.0


def test_buyuk_dususten_kacan_kural_ustun_cikar():
    generator = np.random.default_rng(5)
    returns = generator.normal(0.0015, 0.03, 2_000)
    crash = np.zeros(2_000, dtype=bool)
    for start in (300, 900, 1_500):
        crash[start : start + 150] = True
        returns[start : start + 150] -= 0.01
    rule = np.where(crash, 0.0, returns)
    [test] = trend.difference_tests([rule], returns, resamples=500, block=20, seed=1)
    assert test.difference > 0.5 and test.low > 0
    assert test.p_value < 0.01


def test_ustunluk_yokken_p_degerleri_asiri_kucuk_cikmaz():
    """Kuralın al-ve-tut'a gerçek üstünlüğü olmayan (eğilimsiz, sürüklenmesiz) veride
    sınama yanlış alarm vermemeli."""
    p_values = []
    for seed in range(30):
        frame = daily_frame(no_trend(1_300, seed) - 0.001)
        returns, days = trend.evaluation_arrays(frame)
        benchmark = trend.daily_net_returns(np.ones(returns.size, dtype=bool), returns, COST)
        nets = [trend.daily_net_returns(rule.positions(frame)[days], returns, COST)
                for rule in trend.RULES]
        tests = trend.difference_tests(nets, benchmark, resamples=300, block=20, seed=seed)
        p_values.append([test.p_value for test in tests])
    p_values = np.array(p_values)
    assert p_values.mean() > 0.35
    assert np.mean(p_values <= 0.05) <= 0.08


def test_egilim_konmus_veride_kural_gecer():
    frame = daily_frame(regimes(3_000, 8, drift=0.006))
    result = trend.evaluate_symbol(frame, symbol="BTCUSDT", cost=COST, resamples=500)
    [corrected] = trend.apply_correction([result])
    verdicts = {item.rule.key: item.verdict(corrected.benchmark) for item in corrected.rules}
    assert verdicts["sma200"] == "Geçti"
    assert sum(value == "Geçti" for value in verdicts.values()) >= 3


def test_ayni_tohum_ayni_sonuc():
    frame = daily_frame(regimes(1_200, 6))
    first = trend.evaluate_symbol(frame, symbol="SOLUSDT", cost=COST, resamples=200)
    second = trend.evaluate_symbol(frame, symbol="SOLUSDT", cost=COST, resamples=200)
    assert [item.test for item in first.rules] == [item.test for item in second.rules]


# --- karar -----------------------------------------------------------------------


def _result(sharpe: float, drawdown: float, q_value: float) -> trend.RuleResult:
    perf = trend.Performance(0.0, 0.0, drawdown, sharpe, 50.0, 3, 1000)
    test = trend.DifferenceTest(sharpe - 1.0, 0.0, 0.0, 0.01, 10)
    return trend.RuleResult(trend.RULES[0], perf, perf, test, (), True, q_value)


@pytest.mark.parametrize(
    ("sharpe", "drawdown", "q_value", "verdict"),
    [
        (1.2, 40.0, 0.05, "Geçti"),
        (1.2, 40.0, 0.30, "Belirsiz"),
        (0.8, 40.0, 0.01, "Geçmedi"),   # zamanlama gerçek ama al-ve-tut'tan iyi değil
        (1.2, 80.0, 0.01, "Geçmedi"),   # düşüşü daha derin
    ],
)
def test_karar_on_kayittaki_gibi(sharpe, drawdown, q_value, verdict):
    benchmark = trend.Performance(0.0, 0.0, 70.0, 1.0, 100.0, 1, 1000)
    assert _result(sharpe, drawdown, q_value).verdict(benchmark) == verdict


def test_duzeltme_on_sinamayi_tek_aile_sayar():
    frames = [daily_frame(regimes(1_300, seed)) for seed in (1, 2)]
    results = [trend.evaluate_symbol(frame, symbol=name, cost=COST, resamples=100)
               for frame, name in zip(frames, ("BTCUSDT", "SOLUSDT"), strict=True)]
    corrected = trend.apply_correction(results)
    q_values = [item.q_value for result in corrected for item in result.rules]
    p_values = [item.test.p_value for result in results for item in result.rules]
    assert len(q_values) == 10
    assert all(q >= p for q, p in zip(q_values, p_values, strict=True))


# --- komut satırı ------------------------------------------------------------------


class FakeKlines:
    """``GET /api/v3/klines``'ı taklit eder: sayfa başına en fazla 1000 mum."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls = 0

    def server_time(self) -> int:
        return int(dt.datetime.now(dt.UTC).timestamp() * 1000)

    def klines(self, *, symbol, interval, start_time=None, end_time=None, limit=1000):
        self.calls += 1
        frame = self.frames[symbol]
        rows = frame[(frame["open_time"] >= start_time) & (frame["open_time"] <= end_time)]
        out = []
        for row in rows.head(limit).itertuples():
            out.append([row.open_time, str(row.open), str(row.high), str(row.low),
                        str(row.close), str(row.volume), row.open_time + DAY_MS - 1,
                        str(row.quote_volume), row.trades, str(row.taker_buy_base),
                        str(row.taker_buy_quote), "0"])
        assert all(len(item) == len(KLINE_COLUMNS) for item in out)
        return out


@pytest.fixture
def market():
    today = dt.datetime.now(dt.UTC).date()
    # Bugünün kapanmamış mumu da gelir; araç onu atmalı.
    return {
        "BTCUSDT": daily_frame(regimes(3_000, 11), end=today),
        "SOLUSDT": daily_frame(no_trend(2_000, 12), end=today),
    }


def test_indirme_birkac_istekle_biter_ve_kapanmamis_mumu_atar(market):
    source = FakeKlines(market)
    now = source.server_time()
    frame = trend_cli.download(source, "BTCUSDT", now)
    assert source.calls == 3
    assert len(frame) == 2_999
    assert frame["open_time"].max() < now - DAY_MS + 1


def test_beklenmedik_buyuklukte_indirme_istek_gondermeden_durur(market):
    source = FakeKlines(market)
    far_future = int(dt.datetime(2045, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
    with pytest.raises(trend_cli.DataProblem, match="hiçbir istek gönderilmedi"):
        trend_cli.download(source, "BTCUSDT", far_future)
    assert source.calls == 0


def test_bosluklu_ya_da_eski_veride_sinama_baslamaz(market):
    now = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    frame = market["BTCUSDT"].iloc[:-1]
    trend_cli.check_data(frame, "BTCUSDT", now)  # dün biten veri: sorun yok
    gappy = frame.drop(frame.index[100:120])
    with pytest.raises(trend_cli.DataProblem, match="sorunlu"):
        trend_cli.check_data(gappy, "BTCUSDT", now)
    with pytest.raises(trend_cli.DataProblem, match="güncel değil"):
        trend_cli.check_data(frame.iloc[:-10], "BTCUSDT", now)


def test_maliyet_onceki_turlarla_ayni_tanim(tmp_path):
    cost, detail = trend_cli.cost_per_side(tmp_path, None, None)
    assert cost == pytest.approx(0.0014)
    assert "güvenlik payı" in detail


def test_komut_bir_kez_calisir_ve_raporu_yazar(market, tmp_path, monkeypatch, capsys):
    source = FakeKlines(market)
    monkeypatch.setattr(trend_cli, "PublicHttp", lambda: source)
    monkeypatch.setattr(trend_cli, "enable_system_trust", lambda: None)
    report = tmp_path / "trend-sonuc.txt"
    args = ["--veri-dizini", str(tmp_path / "veri"), "--rapor", str(report), "--orneklem", "200"]

    assert trend_cli.main(args) == 0
    text = report.read_text(encoding="utf-8")
    assert "GÜNLÜK TREND TESTİ" in text and "KARAR" in text
    assert "10 sınama (5 kural × 2 coin)" in text
    assert KlineStore(tmp_path / "veri").exists("SOLUSDT", "1d")
    out = capsys.readouterr().out
    assert "SOLUSDT: 200/200 dönem" in out
    calls = source.calls

    assert trend_cli.main(args) == 1
    assert source.calls == calls, "ikinci çalıştırma hiçbir istek göndermemeli"
    assert "daha önce tamamlanmış" in capsys.readouterr().err


def test_veri_sorununda_rapor_yazilmaz_ve_calistirma_sayilmaz(market, tmp_path, monkeypatch):
    short = {"BTCUSDT": market["BTCUSDT"], "SOLUSDT": market["SOLUSDT"].iloc[-500:]}
    monkeypatch.setattr(trend_cli, "PublicHttp", lambda: FakeKlines(short))
    monkeypatch.setattr(trend_cli, "enable_system_trust", lambda: None)
    report = tmp_path / "trend-sonuc.txt"
    code = trend_cli.main(["--veri-dizini", str(tmp_path), "--rapor", str(report),
                           "--orneklem", "50"])
    assert code == 2 and not report.exists()


def test_rapor_kararlari_ve_dususleri_yazar():
    frames = [daily_frame(regimes(1_400, seed)) for seed in (21, 22)]
    results = trend.apply_correction([
        trend.evaluate_symbol(frame, symbol=name, cost=COST, resamples=100)
        for frame, name in zip(frames, ("BTCUSDT", "SOLUSDT"), strict=True)
    ])
    text = render(results, [], ran_at=dt.datetime(2026, 9, 28, tzinfo=dt.UTC),
                  commit="abc1234", cost_detail="deneme", resamples=100, block=20,
                  seed=1)
    for rule in trend.RULES:
        assert text.count(rule.name_tr) >= 6
    assert "Al-ve-tut'un en derin düşüşleri" in text
    assert "Bir gün geç uygulama" in text
    assert "yatırım tavsiyesi değildir" in text
