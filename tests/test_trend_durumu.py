"""Günlük trend durumu göstergesi (docs/TREND-SONUC.md §6-§7).

* Gösterge sınamadaki kuralın kendisini kullanır; durum, değişim günü ve
  eşik kuralın tanımıyla birebir.
* Sınamanın görmediği günlerde kural ve al-ve-tut, sınamadaki maliyetle.
* Uç her zaman 200 döner; veri yoksa ya da azsa nedenini yazar.
* Canlı döngü günlük mumu yalnızca yeni kapanmış gün eksikken ister.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
from fastapi.testclient import TestClient
from test_canli_dongu import WallClock, make_runner
from test_trend import daily_frame, regimes

from albsat.api.app import AppState, create_app
from albsat.data.store import KlineStore
from albsat.research import trend
from albsat.strategy import trend_status

DAY_MS = 86_400_000
TEST_DAY = dt.date(2026, 10, 18)


def _ms(day: dt.date) -> int:
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC).timestamp() * 1000)


def _frame(count: int = 900, *, end: dt.date = TEST_DAY + dt.timedelta(days=30), seed: int = 3):
    return daily_frame(regimes(count, seed), end=end)


def test_durum_esik_ve_degisim_gunu_kuralin_tanimiyla_ayni():
    frame = _frame()
    now = _ms(TEST_DAY + dt.timedelta(days=31)) + 3_600_000
    status = trend_status.evaluate(frame, now_ms=now)

    rule = next(item for item in trend.RULES if item.key == "kirilim_20_10")
    positions = rule.positions(frame)
    close = frame["close"].to_numpy()
    assert status.state == (trend_status.HOLD if positions[-1] else trend_status.CASH)
    changed = np.flatnonzero(positions[1:] != positions[:-1]) + 1
    assert status.since_open_ms == int(frame["open_time"].iloc[changed[-1]])
    expected = close[-10:].min() if positions[-1] else close[-20:].max()
    assert status.trigger == float(expected)
    assert status.stale_days == 0


def test_sinamadan_sonrasi_sinamadaki_maliyetle_olculur():
    frame = _frame()
    now = _ms(TEST_DAY + dt.timedelta(days=31))
    status = trend_status.evaluate(frame, now_ms=now)
    assert status.since_test is not None and status.since_test.days == 30

    times = frame["open_time"].to_numpy()
    start = int(np.flatnonzero(times == _ms(TEST_DAY))[0])
    close = frame["close"].to_numpy()
    cost = trend_status.COST_PER_SIDE
    hold = (close[-1] / close[start] * (1 - cost) ** 2 - 1) * 100
    assert abs(status.since_test.hold_pct - hold) < 1e-9

    rule = next(item for item in trend.RULES if item.key == "kirilim_20_10")
    held = rule.positions(frame)[start:-1]
    growth = 1.0
    previous = 0
    for index, position in enumerate(held):
        if position != previous:
            growth *= 1 - cost
        if position:
            growth *= close[start + index + 1] / close[start + index]
        previous = position
    if previous:
        growth *= 1 - cost
    assert abs(status.since_test.rule_pct - (growth - 1) * 100) < 1e-9


def test_sinama_gunu_son_gunse_sonrasi_yok():
    frame = _frame(end=TEST_DAY)
    status = trend_status.evaluate(frame, now_ms=_ms(TEST_DAY + dt.timedelta(days=1)))
    assert status.state is not None and status.since_test is None
    # Sınamanın son gününü içermeyen eski veri: sonrası hesaplanmaz, durum yine var.
    old = _frame(end=TEST_DAY - dt.timedelta(days=200))
    status = trend_status.evaluate(old, now_ms=_ms(TEST_DAY))
    assert status.since_test is None and status.stale_days == 199


def test_veri_yoksa_ya_da_azsa_durum_gosterilmez():
    empty = trend_status.evaluate(_frame().iloc[0:0], now_ms=_ms(TEST_DAY))
    assert empty.state is None and "verisi yok" in (empty.problem or "")
    short = trend_status.evaluate(_frame().tail(60), now_ms=_ms(TEST_DAY))
    assert short.state is None and "en az" in (short.problem or "")


def test_uc_her_zaman_200_doner_ve_bayatligi_yazar(tmp_path):
    client = TestClient(create_app(AppState(veri_dizini=tmp_path)), base_url="http://127.0.0.1")
    response = client.get("/api/trend-durumu")
    assert response.status_code == 200
    body = response.json()
    assert body["durum"] is None and body["sorun"] and body["canli"] is False

    today = dt.datetime.now(dt.UTC).date()
    KlineStore(tmp_path).write(_frame(end=today - dt.timedelta(days=5)),
                               symbol="SOLUSDT", interval="1d")
    body = client.get("/api/trend-durumu").json()
    assert body["durum"] in ("elde_tut", "nakitte_bekle")
    assert body["durum_tr"] in ("Elde tut", "Nakitte bekle")
    assert body["bayat"] is True and body["bayat_gun"] == 4
    assert isinstance(body["esik"], str) and isinstance(body["son_kapanis"], str)
    assert "emir göndermez" in body["not"]


def test_canli_dongu_gunluk_mumu_yalnizca_eksikse_ister(tmp_path):
    clock = WallClock(dt.datetime(2026, 10, 20, 12, 0, 5, tzinfo=dt.UTC))
    runner, _, _, http, _ = make_runner(tmp_path, clock)
    runner.bootstrap()
    store = KlineStore(tmp_path)
    assert ("SOLUSDT", "1d") in http.requests
    assert store.last_open_time("SOLUSDT", "1d") == _ms(dt.date(2026, 10, 19))

    http.requests.clear()
    assert runner.sync_trend() == 0
    clock.advance(hours=11)  # 23:00 UTC: bugünün mumu henüz kapanmadı
    assert runner.sync_trend() == 0
    assert ("SOLUSDT", "1d") not in http.requests

    clock.advance(hours=2)  # ertesi gün 01:00 UTC: 20 Ekim'in mumu kapandı
    assert runner.sync_trend() == 1
    assert http.requests == [("SOLUSDT", "1d")]
    assert store.last_open_time("SOLUSDT", "1d") == _ms(dt.date(2026, 10, 20))
