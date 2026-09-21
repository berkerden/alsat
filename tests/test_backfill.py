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
