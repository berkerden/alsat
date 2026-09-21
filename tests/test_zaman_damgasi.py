"""Zaman damgası birimi testleri — 293.759 sahte boşluk hatasının kaydı.

Binance toplu arşivleri (``data.binance.vision``) **1 Ocak 2025'ten itibaren**
zaman damgalarını mikrosaniye cinsinden yayımlıyor, REST uç noktası
``GET /api/v3/klines`` ise milisaniye döndürüyor. Kaynak:
binance/binance-public-data README — "The timestamp for SPOT Data from
January 1st 2025 onwards will be in microseconds."

Düzeltilmeden önce: arşivden gelen 1m mumları arasındaki fark 60.000.000
olarak okunuyordu, beklenen 60.000 ile karşılaştırılıyordu ve **ardışık her
mum çifti boşluk sanılıyordu**. 180 günlük BTCUSDT 1m verisinde bu 293.759
sahte boşluk, yani yüz binlerce gereksiz REST isteği ve IP yasağı riski
demekti.

Bu dosya iki şeyi birlikte sabitler: (1) mikrosaniye veri doğru okunuyor,
(2) okunmasaydı bile onarım yüz binlerce istek atmadan duruyor.
"""
import io
import zipfile

import pandas as pd
import pytest

from albsat.data.backfill import MAX_LIMIT, TooManyGaps, find_gaps, repair
from albsat.data.klines import check_quality, normalize_epoch_ms, parse_klines
from albsat.data.store import KlineStore

#: 2025-01-01 00:00:00 UTC, dört birimde aynı an.
AN_SANIYE = 1_735_689_600
AN_MS = 1_735_689_600_000
AN_MIKRO = 1_735_689_600_000_000
AN_NANO = 1_735_689_600_000_000_000

STEP_MS = 60_000


def arsiv_satiri(index: int, *, birim: int) -> list:
    """2025 sonrası arşiv CSV'sinin bir satırı; ``birim`` çarpanıyla."""
    open_time = (AN_MS + index * STEP_MS) * birim
    close_time = open_time + STEP_MS * birim - 1
    return [open_time, "93576.00000000", "93576.01000000", "93490.00000000",
            "93526.28000000", "10.51383000", close_time, "983605.56466580",
            1544, "4.47298000", "418449.23088490", "0"]


# --- normalize_epoch_ms ---------------------------------------------------

@pytest.mark.parametrize("deger", [AN_SANIYE, AN_MS, AN_MIKRO, AN_NANO])
def test_her_birim_milisaniyeye_cevrilir(deger):
    assert normalize_epoch_ms([deger]).iloc[0] == AN_MS


def test_karisik_birimli_sutun_satir_satir_duzeltilir():
    # Yarısı arşivden (mikrosaniye), yarısı REST'ten (milisaniye) gelmiş
    # eski bir kayıt dosyası böyle görünür.
    karisik = [AN_MIKRO, AN_MS + STEP_MS, AN_MIKRO + 2 * STEP_MS * 1000]
    beklenen = [AN_MS, AN_MS + STEP_MS, AN_MS + 2 * STEP_MS]
    assert list(normalize_epoch_ms(karisik)) == beklenen


def test_tanimsiz_buyukluge_dokunulmaz():
    # Sıfır ve küçük test değerleri sessizce başka bir tarihe kaydırılmaz.
    assert list(normalize_epoch_ms([0, 1, 60_000])) == [0, 1, 60_000]


def test_nanosaniye_cevrimi_tasma_uretmez():
    # int64 sınırı 9,2e18; nanosaniye değerleri 1,7e18 civarında. Tüm diziyi
    # çarpıp maskeleyen bir uygulama burada sessizce negatif sayı üretirdi.
    seri = normalize_epoch_ms([AN_NANO, AN_NANO + 60_000_000_000])
    assert list(seri) == [AN_MS, AN_MS + STEP_MS]
    assert (seri > 0).all()


# --- Gerçek hatanın yeniden üretimi ---------------------------------------

def mikro_arsiv_zip(count: int = 500) -> bytes:
    """2025 sonrası biçiminde, mikrosaniye zaman damgalı arşiv dosyası.

    Bu Berk'in indirdiği dosyanın birebir kopyası değil; biçimi ve birimi
    Binance'in yayımlanmış belgesine göre yeniden üretilmiş eşdeğeridir.
    Bu ortamın ağ politikası data.binance.vision adresini engellediği için
    gerçek dosya buradan indirilemiyor.
    """
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,"
             "count,taker_buy_volume,taker_buy_quote_volume,ignore"]
    for index in range(count):
        lines.append(",".join(str(alan) for alan in arsiv_satiri(index, birim=1000)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-1m-2025-01-01.csv", "\n".join(lines))
    return buffer.getvalue()


def test_duzeltilmemis_veri_her_mum_ciftini_bosluk_sanardi():
    # Hatanın kendisi: ayrıştırıcıyı atlayıp ham mikrosaniyeyi tabloya
    # koyarsak 500 mumda 499 "boşluk" çıkar.
    ham = pd.DataFrame({"open_time": [AN_MIKRO + i * STEP_MS * 1000
                                      for i in range(500)]})
    assert len(find_gaps(ham, interval="1m")) == 499


def test_mikrosaniye_arsivi_dogru_okunur_ve_bosluk_uretmez():
    from albsat.data.vision import read_archive

    frame = read_archive(mikro_arsiv_zip(500), interval="1m")

    assert len(frame) == 500
    assert int(frame["open_time"].iloc[0]) == AN_MS
    assert int(frame["open_time"].iloc[-1]) == AN_MS + 499 * STEP_MS
    # Asıl kanıt: 499 boşluk yerine sıfır.
    assert find_gaps(frame, interval="1m") == []

    report = check_quality(frame, symbol="BTCUSDT", interval="1m")
    assert report.missing_candles == 0
    assert report.completeness_pct == 100.0
    assert report.usable


def test_arsiv_ve_rest_verisi_ayni_eksene_oturur():
    # Arşiv mikrosaniye, REST milisaniye; ikisi birleşince bitişik olmalı.
    from albsat.data.backfill import merge_frames

    arsiv = parse_klines([arsiv_satiri(i, birim=1000) for i in range(10)],
                         interval="1m")
    rest = parse_klines([arsiv_satiri(i, birim=1) for i in range(10, 20)],
                        interval="1m")
    birlesik = merge_frames([arsiv, rest])

    assert len(birlesik) == 20
    assert find_gaps(birlesik, interval="1m") == []


def test_kayitli_bozuk_dosya_okunurken_onarilir(tmp_path):
    # Önceki çalıştırmadan kalan mikrosaniyeli Parquet, silinmeye gerek
    # kalmadan okunurken düzeltilir.
    store = KlineStore(tmp_path)
    bozuk = parse_klines([arsiv_satiri(i, birim=1) for i in range(10)],
                         interval="1m").copy()
    bozuk["open_time"] = bozuk["open_time"] * 1000
    store.write(bozuk, symbol="BTCUSDT", interval="1m")

    okunan = store.read("BTCUSDT", "1m")
    assert int(okunan["open_time"].iloc[0]) == AN_MS
    assert find_gaps(okunan, interval="1m") == []


# --- Güvenlik kemeri ------------------------------------------------------

class SayanKaynak:
    """İstek sayan sahte borsa; güvenlik kemeri sızıntısını yakalar."""

    def __init__(self):
        self.calls = []

    def klines(self, *, symbol, interval, start_time=None, end_time=None,
               limit=MAX_LIMIT):
        self.calls.append((start_time, end_time))
        return []


def test_sacma_bosluk_sayisinda_tek_istek_bile_yapilmaz():
    ham = pd.DataFrame({"open_time": [AN_MIKRO + i * STEP_MS * 1000
                                      for i in range(600)]})
    kaynak = SayanKaynak()
    with pytest.raises(TooManyGaps) as hata:
        repair(ham, kaynak, symbol="BTCUSDT", interval="1m")

    assert kaynak.calls == []
    assert "599" in str(hata.value)
    assert "bozuk" in str(hata.value)


def test_makul_bosluk_sayisi_normal_doldurulur():
    # Güvenlik kemeri gerçek boşlukları engellemiyor.
    ham = parse_klines(
        [arsiv_satiri(i, birim=1) for i in range(100) if i not in (5, 40)],
        interval="1m",
    )
    kaynak = SayanKaynak()
    repair(ham, kaynak, symbol="BTCUSDT", interval="1m")
    assert len(kaynak.calls) == 2


def test_bozuk_satir_devasa_bosluk_uretmez():
    # Kısa kesilmiş bir CSV satırı eskiden open_time=0 olarak tabloya
    # girerdi; find_gaps onu ilk gerçek mumla arasında 56 yıllık tek bir
    # boşluk sanar, onarım da binlerce istek yapardı.
    satirlar = [arsiv_satiri(i, birim=1000) for i in range(5)]
    satirlar.insert(2, ["", "", "", "", "", "", "", "", "", "", "", ""])

    frame = parse_klines(satirlar, interval="1m")

    assert len(frame) == 5
    assert int(frame["open_time"].min()) == AN_MS
    assert find_gaps(frame, interval="1m") == []
