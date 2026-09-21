"""Arşiv indirme ve ayrıştırma testleri (ağa çıkmaz)."""
import datetime as dt
import hashlib
import io
import zipfile

import pytest

from albsat.data.vision import (
    ArchiveRef, fetch_archive, plan_archives, read_archive, verify_checksum,
)

BASE = 1_700_000_000_000


def make_zip(*, with_header: bool, count: int = 3) -> bytes:
    lines = []
    if with_header:
        lines.append(
            "open_time,open,high,low,close,volume,close_time,quote_volume,"
            "count,taker_buy_volume,taker_buy_quote_volume,ignore"
        )
    for index in range(count):
        open_time = BASE + index * 60_000
        lines.append(
            f"{open_time},100.0,101.0,99.0,100.5,10.0,{open_time + 59_999},"
            "1005.0,5,5.0,502.5,0"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-1m-2026-08.csv", "\n".join(lines))
    return buffer.getvalue()


def test_url_kalibi_dokumanla_uyusur():
    ref = ArchiveRef("BTCUSDT", "1m", "2026-08", "monthly")
    assert ref.url == (
        "https://data.binance.vision/data/spot/monthly/klines/"
        "BTCUSDT/1m/BTCUSDT-1m-2026-08.zip"
    )
    assert ref.checksum_url.endswith(".zip.CHECKSUM")


def test_plan_tam_aylari_aylik_son_ayi_gunluk_alir():
    # today açıkça verilir; aksi halde test gerçek tarihe göre değişirdi.
    plan = plan_archives("BTCUSDT", "1m", dt.date(2026, 6, 15), dt.date(2026, 9, 21),
                         today=dt.date(2026, 9, 21))
    monthly = [r for r in plan if r.granularity == "monthly"]
    daily = [r for r in plan if r.granularity == "daily"]
    assert [r.period for r in monthly] == ["2026-06", "2026-07", "2026-08"]
    # Bugünün (21'inin) arşivi henüz yayımlanmadığı için plana girmez.
    assert len(daily) == 20
    assert daily[0].period == "2026-09-01"
    assert daily[-1].period == "2026-09-20"


def test_bugunun_arsivi_hic_istenmez():
    # Binance günlük arşivi ancak gün bittikten sonra yayımlar; bugünü
    # istemek kesin 404 demektir.
    today = dt.date(2026, 9, 21)
    plan = plan_archives("BTCUSDT", "1m", dt.date(2026, 9, 18), today, today=today)
    assert all(r.period != today.isoformat() for r in plan)
    assert plan[-1].period == "2026-09-20"


def test_bugun_baslayan_aralik_bos_plan_dondurur():
    today = dt.date(2026, 9, 21)
    assert plan_archives("BTCUSDT", "1m", today, today, today=today) == []


@pytest.mark.parametrize("with_header", [True, False])
def test_baslikli_ve_baslisiz_csv_ayni_sonucu_verir(with_header):
    # 2025 sonrası arşivler başlık satırı içeriyor, eskiler içermiyor.
    frame = read_archive(make_zip(with_header=with_header), interval="1m")
    assert len(frame) == 3
    assert frame["is_closed"].all()
    assert frame["open_time"].iloc[0] == BASE


def test_bozuk_dosya_reddedilir():
    payload = make_zip(with_header=True)
    with pytest.raises(ValueError, match="sağlama hatası"):
        verify_checksum(b"bozuk", hashlib.sha256(payload).hexdigest(), filename="x.zip")


def test_dogru_saglama_gecer():
    payload = make_zip(with_header=True)
    verify_checksum(payload, hashlib.sha256(payload).hexdigest() + "  x.zip",
                    filename="x.zip")


def test_fetch_archive_sagladigi_dosyayi_ayristirir():
    payload = make_zip(with_header=True)

    class FakeDownloader:
        def get(self, url):
            if url.endswith(".CHECKSUM"):
                return (hashlib.sha256(payload).hexdigest() + "  x.zip").encode()
            return payload

    frame = fetch_archive(ArchiveRef("BTCUSDT", "1m", "2026-08", "monthly"),
                          FakeDownloader())
    assert len(frame) == 3


def test_csv_olmayan_arsiv_hata_verir():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("okuma.txt", "merhaba")
    with pytest.raises(ValueError, match="CSV"):
        read_archive(buffer.getvalue(), interval="1m")


def test_fetch_many_sirayi_korur_ve_ilerleme_bildirir():
    from albsat.data.vision import fetch_many

    payloads = {}
    refs = [ArchiveRef("BTCUSDT", "1m", f"2026-09-{day:02d}", "daily")
            for day in range(1, 9)]
    for index, ref in enumerate(refs):
        payloads[ref.filename] = make_zip(with_header=True, count=index + 1)

    class FakeDownloader:
        def get(self, url):
            name = url.rsplit("/", 1)[-1].removesuffix(".CHECKSUM")
            payload = payloads[name]
            if url.endswith(".CHECKSUM"):
                return (hashlib.sha256(payload).hexdigest() + f"  {name}").encode()
            return payload

    seen = []
    frames = fetch_many(refs, FakeDownloader(), workers=4,
                        on_result=lambda i, r, f, e: seen.append((i, r.filename)))

    # Paralel indirilse de sonuçlar istenen sırada dönmeli.
    assert [len(f) for f in frames] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [name for _, name in seen] == [r.filename for r in refs]
    assert [i for i, _ in seen] == list(range(1, 9))


def test_fetch_many_hatali_dosyada_durmaz():
    from albsat.data.vision import fetch_many

    payload = make_zip(with_header=True)
    refs = [ArchiveRef("BTCUSDT", "1m", f"2026-09-0{day}", "daily") for day in (1, 2, 3)]

    class PartialDownloader:
        def get(self, url):
            if "2026-09-02" in url:
                raise OSError("indirilemedi")
            if url.endswith(".CHECKSUM"):
                return (hashlib.sha256(payload).hexdigest() + "  x.zip").encode()
            return payload

    errors = []
    frames = fetch_many(refs, PartialDownloader(), workers=2,
                        on_result=lambda i, r, f, e: errors.append(e))

    # Bir dosya hata verse de diğerleri alınır.
    assert len(frames) == 2
    assert sum(1 for e in errors if e is not None) == 1


def test_is_cached_onbellekteki_dosyayi_bulur(tmp_path):
    from albsat.data.vision import is_cached

    ref = ArchiveRef("BTCUSDT", "1m", "2026-08", "monthly")
    assert not is_cached(ref, tmp_path)
    target = tmp_path / "BTCUSDT" / "1m" / ref.filename
    target.parent.mkdir(parents=True)
    target.write_bytes(make_zip(with_header=True))
    assert is_cached(ref, tmp_path)
    assert not is_cached(ref, None)


def test_onbellekteki_dosya_tekrar_indirilmez(tmp_path):
    payload = make_zip(with_header=True)
    target = tmp_path / "BTCUSDT" / "1m" / "BTCUSDT-1m-2026-08.zip"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)

    class ExplodingDownloader:
        def get(self, url):
            raise AssertionError("önbellekte olmasına rağmen indirme denendi")

    frame = fetch_archive(ArchiveRef("BTCUSDT", "1m", "2026-08", "monthly"),
                          ExplodingDownloader(), cache_dir=tmp_path)
    assert len(frame) == 3
