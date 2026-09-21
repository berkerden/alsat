"""Mum verisi ve kalite kontrolü testleri."""
import pytest

from albsat.data.klines import check_quality, closed_only, interval_ms, parse_klines

BASE = 1_700_000_000_000


def rows(count, *, skip=(), volume="10.0"):
    out = []
    for index in range(count):
        if index in skip:
            continue
        open_time = BASE + index * 60_000
        out.append([open_time, "100.0", "101.0", "99.0", "100.5", volume,
                    open_time + 59_999, "1005.0", 5, "5.0", "502.5", "0"])
    return out


def test_kapanmamis_mum_isaretlenir():
    # SPEC.md §11: kapanmamış mum tahminde kullanılamaz.
    frame = parse_klines(rows(3), interval="1m", now_ms=BASE + 2 * 60_000 + 30_000)
    assert list(frame["is_closed"]) == [True, True, False]
    assert len(closed_only(frame)) == 2


def test_arsiv_verisinde_tum_mumlar_kapanmistir():
    frame = parse_klines(rows(3), interval="1m")
    assert frame["is_closed"].all()


def test_bos_girdi_bos_cerceve_dondurur():
    assert parse_klines([], interval="1m").empty


def test_eksik_mum_tespit_edilir():
    report = check_quality(parse_klines(rows(10, skip=(5,)), interval="1m"),
                           symbol="X", interval="1m")
    assert report.missing_candles == 1
    assert len(report.gaps) == 1
    assert report.completeness_pct == 90.0
    assert not report.usable


def test_eksiksiz_veri_kullanilabilir():
    report = check_quality(parse_klines(rows(100), interval="1m"),
                           symbol="X", interval="1m")
    assert report.missing_candles == 0
    assert report.completeness_pct == 100.0
    assert report.usable


def test_sifir_hacim_sayilir():
    report = check_quality(parse_klines(rows(5, volume="0"), interval="1m"),
                           symbol="X", interval="1m")
    assert report.zero_volume_rows == 5


def test_gecersiz_ohlc_yakalanir():
    bad = rows(3)
    bad[1][2] = "98.0"  # high < low
    report = check_quality(parse_klines(bad, interval="1m"), symbol="X", interval="1m")
    assert report.invalid_ohlc_rows == 1
    assert not report.usable


def test_bilinmeyen_periyot_reddedilir():
    with pytest.raises(ValueError, match="Desteklenmeyen periyot"):
        interval_ms("7m")
