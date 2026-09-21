"""REST boşluk doldurma testleri (ağa çıkmadan, sahte kaynakla)."""
from albsat.data.backfill import MAX_LIMIT, fetch_range, find_gaps, merge_frames, repair
from albsat.data.klines import parse_klines

BASE = 1_700_000_000_000
STEP = 60_000


def kline(index):
    open_time = BASE + index * STEP
    return [open_time, "100.0", "101.0", "99.0", "100.5", "10.0",
            open_time + STEP - 1, "1005.0", 5, "5.0", "502.5", "0"]


class FakeSource:
    """Sayfalama davranışını taklit eden sahte borsa."""

    def __init__(self, available):
        self.available = sorted(available)
        self.calls = []

    def klines(self, *, symbol, interval, start_time=None, end_time=None, limit=MAX_LIMIT):
        self.calls.append((start_time, end_time))
        selected = [
            i for i in self.available
            if (start_time is None or BASE + i * STEP >= start_time)
            and (end_time is None or BASE + i * STEP <= end_time)
        ]
        return [kline(i) for i in selected[:limit]]


def test_bosluklar_bulunur():
    frame = parse_klines([kline(i) for i in (0, 1, 2, 7, 8)], interval="1m")
    assert find_gaps(frame, interval="1m") == [(BASE + 3 * STEP, BASE + 6 * STEP)]


def test_bosluksuz_veride_bosluk_yok():
    frame = parse_klines([kline(i) for i in range(5)], interval="1m")
    assert find_gaps(frame, interval="1m") == []


def test_repair_bosluklari_doldurur():
    frame = parse_klines([kline(i) for i in (0, 1, 2, 7, 8)], interval="1m")
    fixed, added = repair(frame, FakeSource(range(20)), symbol="X", interval="1m")
    assert added == 4
    assert find_gaps(fixed, interval="1m") == []


def test_merge_frames_tekillestirir_ve_yeniyi_tutar():
    eski = parse_klines([kline(0)], interval="1m")
    yeni = parse_klines([kline(0)], interval="1m").copy()
    yeni.loc[0, "close"] = 999.0
    merged = merge_frames([eski, yeni])
    assert len(merged) == 1
    assert merged["close"].iloc[0] == 999.0


def test_ilerlemeyen_yanit_sonsuz_donguye_girmez():
    class Stuck:
        def klines(self, **kwargs):
            return [kline(0)]  # her zaman aynı mum

    frame = fetch_range(Stuck(), symbol="X", interval="1m",
                        start_time=BASE, end_time=BASE + 100 * STEP)
    assert len(frame) == 1


def test_fetch_range_bos_kaynakta_bos_doner():
    class Empty:
        def klines(self, **kwargs):
            return []

    assert fetch_range(Empty(), symbol="X", interval="1m",
                       start_time=BASE, end_time=BASE + STEP).empty


def test_extend_to_now_arsivin_bittigi_yerden_devam_eder():
    from albsat.data.backfill import extend_to_now

    # Arşiv 0..4 arasını kapsıyor; borsada 0..9 var.
    frame = parse_klines([kline(i) for i in range(5)], interval="1m")
    source = FakeSource(range(10))
    extended, added = extend_to_now(
        frame, source, symbol="X", interval="1m", now_ms=BASE + 9 * STEP
    )
    assert added == 5
    assert int(extended["open_time"].max()) == BASE + 9 * STEP
    # İlk istek, elimizdeki son mumun bir sonrasından başlamalı.
    assert source.calls[0][0] == BASE + 5 * STEP


def test_extend_to_now_guncel_veride_istek_yapmaz():
    from albsat.data.backfill import extend_to_now

    frame = parse_klines([kline(i) for i in range(5)], interval="1m")
    source = FakeSource(range(10))
    _, added = extend_to_now(
        frame, source, symbol="X", interval="1m", now_ms=BASE + 4 * STEP
    )
    assert added == 0
    assert source.calls == []


def test_extend_to_now_bos_veride_bos_doner():
    from albsat.data.backfill import extend_to_now

    empty = parse_klines([], interval="1m")
    result, added = extend_to_now(
        empty, FakeSource(range(10)), symbol="X", interval="1m", now_ms=BASE
    )
    assert added == 0
    assert result.empty


def test_repair_ilerlemeyi_bildirir():
    frame = parse_klines([kline(i) for i in (0, 1, 5, 6, 10)], interval="1m")
    olaylar = []
    repair(frame, FakeSource(range(20)), symbol="X", interval="1m",
           on_progress=lambda done, total: olaylar.append((done, total)))
    # İki boşluk var; her biri bittiğinde bildirilmeli.
    assert olaylar == [(1, 2), (2, 2)]


def test_repair_bosluk_yoksa_sifir_bildirir():
    frame = parse_klines([kline(i) for i in range(5)], interval="1m")
    olaylar = []
    repair(frame, FakeSource(range(20)), symbol="X", interval="1m",
           on_progress=lambda done, total: olaylar.append((done, total)))
    assert olaylar == [(0, 0)]
