"""16 Kasım 2026 gözden geçirmesinde eklenen sağlamlık ve verim düzeltmeleri.

Hiçbiri alım-satım davranışını ya da hesap sonuçlarını değiştirmez:

* mum deposu son okunan dosyaları bellekte tutar (dosya değişince bayat dönmez),
* sinyal günlüğü bağlantılarını kapatır,
* bütçesiz (komut satırı) istekler 429'da borsanın istediği süre kadar bekler.
"""

from __future__ import annotations

import gc
import io
import urllib.error
import warnings

import pandas as pd
import pytest

from albsat.data import store as store_module
from albsat.data.store import KlineStore
from albsat.exchange import http as http_module
from albsat.exchange.http import HttpError, PublicHttp
from albsat.strategy.journal import SignalJournal


def _frame(count: int, *, start: int = 1_700_000_000_000) -> pd.DataFrame:
    times = [start + index * 900_000 for index in range(count)]
    return pd.DataFrame(
        {
            "open_time": times,
            "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
            "is_closed": True,
        }
    )


# --- mum deposu ---------------------------------------------------------


def test_depo_ayni_dosyayi_ikinci_kez_diskten_okumaz(tmp_path, monkeypatch):
    store = KlineStore(tmp_path)
    store.write(_frame(5), symbol="BTCUSDT", interval="15m")
    reads: list[object] = []
    real = pd.read_parquet

    def counting(path, *args, **kwargs):
        reads.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(store_module.pd, "read_parquet", counting)
    first = store.read("BTCUSDT", "15m")
    second = KlineStore(tmp_path).read("BTCUSDT", "15m")  # başka nesne, aynı dosya
    assert store.last_open_time("BTCUSDT", "15m") == int(first["open_time"].max())
    assert len(reads) == 1
    pd.testing.assert_frame_equal(first, second)


def test_depo_yazimdan_sonra_yeni_veriyi_dondurur(tmp_path):
    store = KlineStore(tmp_path)
    store.write(_frame(5), symbol="BTCUSDT", interval="15m")
    assert len(store.read("BTCUSDT", "15m")) == 5
    store.upsert(_frame(7), symbol="BTCUSDT", interval="15m")
    assert len(store.read("BTCUSDT", "15m")) == 7
    assert store.last_open_time("BTCUSDT", "15m") == int(_frame(7)["open_time"].max())


def test_depo_baska_surecin_yazdigini_gorur(tmp_path):
    """Veri tazeleme ayrı bir süreçtir; bellekteki kopya dosya değişince geçersizdir."""
    store = KlineStore(tmp_path)
    store.write(_frame(5), symbol="BTCUSDT", interval="15m")
    assert len(store.read("BTCUSDT", "15m")) == 5
    path = store.path_for("BTCUSDT", "15m")
    temporary = path.with_suffix(".baska")
    _frame(9).to_parquet(temporary, index=False)  # depo sınıfından geçmeden
    temporary.replace(path)
    assert len(store.read("BTCUSDT", "15m")) == 9


def test_depodan_okunan_tablo_degistirilince_bellekteki_bozulmaz(tmp_path):
    store = KlineStore(tmp_path)
    store.write(_frame(5), symbol="BTCUSDT", interval="15m")
    frame = store.read("BTCUSDT", "15m")
    frame.loc[:, "close"] = -1.0
    frame.drop(frame.index[:3], inplace=True)
    again = store.read("BTCUSDT", "15m")
    assert len(again) == 5 and (again["close"] == 1.5).all()


def test_depo_silinen_dosyada_bos_doner(tmp_path):
    store = KlineStore(tmp_path)
    store.write(_frame(5), symbol="BTCUSDT", interval="15m")
    assert not store.read("BTCUSDT", "15m").empty
    store.path_for("BTCUSDT", "15m").unlink()
    assert store.read("BTCUSDT", "15m").empty
    assert store.last_open_time("BTCUSDT", "15m") is None


# --- sinyal günlüğü --------------------------------------------------------


def test_sinyal_gunlugu_baglanti_acik_birakmaz(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        journal = SignalJournal.in_directory(tmp_path)
        assert journal.recent() == ()
        assert journal.open_entries() == ()
        assert journal.performance() == {"sonuclanan": 0}
        gc.collect()


# --- 429 ve Retry-After -------------------------------------------------------


def _too_many(retry_after: str | None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}

    def raiser(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many", headers,
                                     io.BytesIO(b"{}"))

    return raiser


def test_butcesiz_istek_429da_borsanin_istedigi_kadar_bekler(monkeypatch):
    sleeps: list[float] = []
    calls: list[int] = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            _too_many("7")(request, timeout)
        return _Response(b'{"serverTime": 5}')

    monkeypatch.setattr(http_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_module.time, "sleep", sleeps.append)
    assert PublicHttp(retries=2, backoff=1.0).server_time() == 5
    assert sleeps == [7.0]  # 1 sn'lik kendi beklemesi değil, borsanın istediği 7 sn


def test_butcesiz_istek_cok_uzun_bekleme_istenirse_beklemeden_durur(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(http_module.urllib.request, "urlopen", _too_many("900"))
    monkeypatch.setattr(http_module.time, "sleep", sleeps.append)
    with pytest.raises(HttpError):
        PublicHttp(retries=4, backoff=1.0).server_time()
    assert sleeps == []


def test_butcesiz_istek_retry_after_yoksa_eski_beklemeyi_kullanir(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(http_module.urllib.request, "urlopen", _too_many(None))
    monkeypatch.setattr(http_module.time, "sleep", sleeps.append)
    with pytest.raises(HttpError):
        PublicHttp(retries=2, backoff=1.0).server_time()
    assert sleeps == [1.0, 2.0]


class _Response(io.BytesIO):
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
