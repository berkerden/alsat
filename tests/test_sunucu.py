"""Faz 7: yedek, sunucu sır deposu, gözcü, tek kopya kilidi, sunucuda IP kısıtı.

Ağa çıkmaz: gözcü istekleri sahte bir ``opener``'a gider.
"""

from __future__ import annotations

import io
import json
import os
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from albsat.api.app import AppState, create_app
from albsat.api.runtime import MIN_FREE_BYTES, Runtime
from albsat.cli import gozcu as gozcu_cli
from albsat.cli import serve as serve_cli
from albsat.cli import yedek as yedek_cli
from albsat.core import backup, db, keychain
from albsat.core.instance import AlreadyRunning, InstanceLock, held_by_other
from albsat.exchange.keys import SecretText
from albsat.exchange.signed import live_key_problems
from albsat.notify.base import MemoryNotifier
from albsat.notify.gozcu import (
    FAILS_BEFORE_ALARM,
    HealthVerdict,
    PingClient,
    Watchdog,
    WatchdogError,
    valid_url,
)

BASE = "http://127.0.0.1"
URL = "https://hc-ping.com/1f2e3d4c-aaaa-bbbb-cccc-0123456789ab"
T0 = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


# --- yedek ---------------------------------------------------------------------------------


def _database(root: Path, value: str = "ilk") -> Path:
    path = db.path_in(root)
    with db.session(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS deneme (deger TEXT)")
        connection.execute("DELETE FROM deneme")
        connection.execute("INSERT INTO deneme VALUES (?)", (value,))
    return path


def _value(root: Path) -> str:
    with db.session(db.path_in(root)) as connection:
        return str(connection.execute("SELECT deger FROM deneme").fetchone()[0])


def test_yedek_veritabani_ve_ayar_dosyalarini_arsivler(tmp_path):
    _database(tmp_path)
    (tmp_path / "kurallar.json").write_text('{"kurallar": []}', encoding="utf-8")
    (tmp_path / "telegram.json").write_text('{"chat_id": 1}', encoding="utf-8")
    (tmp_path / "BTCUSDT").mkdir()
    (tmp_path / "BTCUSDT" / "1m.parquet").write_bytes(b"mum verisi yedeklenmez")

    result = backup.create(tmp_path, now=T0)

    assert result.yol.name == "albsat-yedek-20260926-120000.tar.gz"
    assert stat.S_IMODE(result.yol.stat().st_mode) == 0o600
    assert set(result.dosyalar) == {"albsat.sqlite3", "kurallar.json", "telegram.json"}
    manifest = backup.verify(result.yol)
    assert manifest["zaman_utc"] == "2026-09-26T12:00:00+00:00"
    with tarfile.open(result.yol) as archive:
        assert not any("parquet" in name for name in archive.getnames())
    assert not any(item.name.startswith(".") for item in backup.directory(tmp_path).iterdir())


def test_yedek_acik_baglanti_ve_wal_varken_tutarli_kopya_alir(tmp_path):
    path = _database(tmp_path)
    writer = db.connect(path)
    writer.execute("INSERT INTO deneme VALUES ('wal içinde')")
    writer.commit()  # kayıt henüz WAL dosyasında, ana dosyada değil
    try:
        result = backup.create(tmp_path, now=T0)
    finally:
        writer.close()
    with tarfile.open(result.yol) as archive:
        data = archive.extractfile("albsat.sqlite3").read()  # type: ignore[union-attr]
    copy = tmp_path / "kopya.sqlite3"
    copy.write_bytes(data)
    rows = sqlite3.connect(copy).execute("SELECT deger FROM deneme").fetchall()
    assert ("wal içinde",) in rows


def test_eski_yedekler_silinir_en_yeniler_kalir(tmp_path):
    _database(tmp_path)
    for day in range(backup.KEEP + 3):
        backup.create(tmp_path, now=T0 + timedelta(days=day))
    names = [item.name for item in backup.list_backups(tmp_path)]
    assert len(names) == backup.KEEP
    assert names == sorted(names, reverse=True)  # en yenisi başta
    assert backup.backup_time(backup.list_backups(tmp_path)[0]) == T0 + timedelta(
        days=backup.KEEP + 2)
    assert backup.latest_age(tmp_path, T0 + timedelta(days=backup.KEEP + 2, hours=3)) == (
        timedelta(hours=3))


def test_veritabani_yoksa_yedek_hata_verir(tmp_path):
    with pytest.raises(backup.BackupError, match="bulunamadı"):
        backup.create(tmp_path, now=T0)


def _rewrite(archive_path: Path, replace: dict[str, bytes]) -> None:
    with tarfile.open(archive_path) as archive:
        items = {m.name: archive.extractfile(m).read() for m in archive.getmembers()}  # type: ignore[union-attr]
    items.update(replace)
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in items.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_bozulan_ya_da_yabanci_arsiv_reddedilir(tmp_path):
    _database(tmp_path)
    result = backup.create(tmp_path, now=T0)
    _rewrite(result.yol, {"kurallar.json": b"{}"})  # manifestte yok ama izinli ad: özet yok
    assert backup.verify(result.yol)  # özetlenmemiş fazladan json geri yüklenmez
    _rewrite(result.yol, {"albsat.sqlite3": b"bozuk"})
    with pytest.raises(backup.BackupError, match="özeti tutmuyor"):
        backup.verify(result.yol)
    _rewrite(result.yol, {"../disari.json": b"{}"})
    with pytest.raises(backup.BackupError, match="beklenmeyen"):
        backup.verify(result.yol)


def test_geri_yukleme_eski_kaydi_getirir_mevcutlari_kenara_tasir(tmp_path):
    _database(tmp_path, "yedekteki")
    (tmp_path / "kurallar.json").write_text('{"eski": true}', encoding="utf-8")
    archive = backup.create(tmp_path, now=T0).yol
    _database(tmp_path, "sonradan")
    (tmp_path / "kurallar.json").write_text('{"yeni": true}', encoding="utf-8")

    result = backup.restore(tmp_path, archive, now=T0 + timedelta(hours=1))

    assert _value(tmp_path) == "yedekteki"
    assert json.loads((tmp_path / "kurallar.json").read_text()) == {"eski": True}
    assert result.eskiler.name == "geri-yukleme-oncesi-20260926-130000"
    assert json.loads((result.eskiler / "kurallar.json").read_text()) == {"yeni": True}
    saved = sqlite3.connect(result.eskiler / "albsat.sqlite3")
    assert saved.execute("SELECT deger FROM deneme").fetchone()[0] == "sonradan"


def test_uygulama_aciksa_geri_yukleme_yapilmaz(tmp_path):
    _database(tmp_path)
    archive = backup.create(tmp_path, now=T0).yol
    with pytest.raises(backup.BackupError, match="çalışıyor"):
        backup.restore(tmp_path, archive, running=lambda: True)
    assert _value(tmp_path) == "ilk"


def test_zamanlayici_gunde_bir_yedek_alir_hatayi_bir_kez_bildirir(tmp_path, monkeypatch):
    clock = [T0]
    failures: list[str] = []
    scheduler = backup.BackupScheduler(tmp_path, on_failure=failures.append,
                                       clock=lambda: clock[0])
    assert scheduler.run_once() is None  # veritabanı yok: yedeklenecek bir şey yok
    _database(tmp_path)
    assert scheduler.run_once() is not None
    clock[0] = T0 + timedelta(hours=23)
    assert scheduler.run_once() is None
    clock[0] = T0 + timedelta(hours=25)
    assert scheduler.run_once() is not None
    assert scheduler.status()["yedek_sayisi"] == 2

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup, "create", full_disk)
    clock[0] = T0 + timedelta(days=3)
    assert scheduler.run_once() is None
    assert scheduler.run_once() is None
    assert len(failures) == 1 and "yedek alınamadı" in failures[0]
    assert "No space" in scheduler.status()["son_hata"]
    monkeypatch.undo()
    assert scheduler.run_once() is not None
    assert scheduler.status()["son_hata"] is None


def test_yedek_komutu_alir_listeler_sinar_ve_geri_yukler(tmp_path, capsys):
    _database(tmp_path, "komut")
    root = ["--veri-dizini", str(tmp_path)]
    assert yedek_cli.main(root) == 0
    archive = backup.list_backups(tmp_path)[0]
    assert yedek_cli.main([*root, "--listele"]) == 0
    assert yedek_cli.main([*root, "--sina", str(archive)]) == 0
    _database(tmp_path, "değişti")
    assert yedek_cli.main([*root, "--port", "1", "--geri-yukle", str(archive)]) == 0
    assert _value(tmp_path) == "komut"
    out = capsys.readouterr().out
    assert "Yedek alındı" in out and "Yedek sağlam" in out and "Geri yüklendi" in out


def test_yedek_komutu_uygulama_aciksa_geri_yuklemez(tmp_path, capsys):
    _database(tmp_path)
    archive = backup.create(tmp_path, now=T0).yol
    with InstanceLock(tmp_path):
        code = yedek_cli.main(["--veri-dizini", str(tmp_path), "--port", "1",
                               "--geri-yukle", str(archive)])
    assert code == 1 and "çalışıyor" in capsys.readouterr().out


# --- sunucu sır deposu -----------------------------------------------------------------------


@pytest.fixture
def sir_dizini(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "sirlar"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv(keychain.DIRECTORY_ENV, str(folder))
    return folder


def test_sir_dizini_yaz_oku_sil(sir_dizini):
    assert keychain.available()
    assert keychain.where("e") == "sunucunun sır dizinine"
    assert keychain.read("albsat-binance-canli", "private-key") is None
    keychain.write("albsat-binance-canli", "private-key", SecretText("QUJDREVGR0g=abc.~"))
    path = sir_dizini / "albsat-binance-canli" / "private-key"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert keychain.read("albsat-binance-canli", "private-key").reveal() == "QUJDREVGR0g=abc.~"
    assert not [item for item in path.parent.iterdir() if item.name.startswith(".yeni-")]
    assert keychain.delete("albsat-binance-canli", "private-key")
    assert keychain.read("albsat-binance-canli", "private-key") is None
    assert not keychain.delete("albsat-binance-canli", "private-key")


def test_sir_dizini_izinleri_aciksa_okunmaz(sir_dizini):
    keychain.write("albsat-gozcu", "ping-adresi", SecretText(URL))
    os.chmod(sir_dizini / "albsat-gozcu" / "ping-adresi", 0o644)
    with pytest.raises(keychain.KeychainError, match="chmod 600"):
        keychain.read("albsat-gozcu", "ping-adresi")
    os.chmod(sir_dizini / "albsat-gozcu" / "ping-adresi", 0o600)
    os.chmod(sir_dizini, 0o755)
    with pytest.raises(keychain.KeychainError, match="chmod 700"):
        keychain.read("albsat-gozcu", "ping-adresi")


def test_sir_dizininde_baglanti_ve_gecersiz_ad_reddedilir(sir_dizini, tmp_path):
    outside = tmp_path / "disarida"
    outside.write_text("gizli-deger", encoding="ascii")
    os.chmod(outside, 0o600)
    (sir_dizini / "albsat-gozcu").mkdir(mode=0o700)
    (sir_dizini / "albsat-gozcu" / "ping-adresi").symlink_to(outside)
    with pytest.raises(keychain.KeychainError, match="bağlantı"):
        keychain.read("albsat-gozcu", "ping-adresi")
    for name in ("..", ".", "a/b"):
        with pytest.raises(keychain.KeychainError, match="geçersiz"):
            keychain.read(name, "x")
    with pytest.raises(keychain.KeychainError, match="beklenmeyen"):
        keychain.write("albsat-gozcu", "ping-adresi", SecretText('abc" ; rm -rf ~ "defgh'))


def test_sir_dizini_yoksa_okuma_bos_yazma_olusturur(tmp_path, monkeypatch):
    folder = tmp_path / "yeni-sirlar"
    monkeypatch.setenv(keychain.DIRECTORY_ENV, str(folder))
    assert keychain.read("albsat-telegram", "bot-token") is None
    keychain.write("albsat-telegram", "bot-token", SecretText("deneme-jetonu-12345"))
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_sir_deposu_yoksa_neden_soylenir(monkeypatch):
    monkeypatch.setattr(keychain.sys, "platform", "linux")
    assert not keychain.available()
    assert "yalnızca Mac" in keychain.unavailable_reason()
    assert keychain.where("de") == "Mac'in Anahtar Zinciri'nde"


# --- sunucuda IP kısıtı zorunlu ------------------------------------------------------------


def _restrictions(**changes: bool) -> dict[str, bool]:
    payload = {
        "ipRestrict": False, "enableReading": True, "enableSpotAndMarginTrading": True,
        "enableWithdrawals": False, "enableInternalTransfer": False, "enableMargin": False,
        "enableFutures": False, "permitsUniversalTransfer": False,
        "enableVanillaOptions": False, "enablePortfolioMarginTrading": False,
    }
    payload.update(changes)
    return payload


def test_sunucuda_ip_kisitsiz_canli_anahtar_engellenir():
    blocking, warnings = live_key_problems(_restrictions(), require_ip=True)
    assert blocking and "sabit IP" in blocking[0] and not warnings
    assert live_key_problems(_restrictions(ipRestrict=True), require_ip=True) == ([], [])
    blocking, warnings = live_key_problems(_restrictions())
    assert not blocking and warnings


def test_canli_istemci_sir_dizini_varsa_ip_kisiti_ister(sir_dizini):
    from fake_binance import FakeBinance
    from test_canli import _trader
    from test_demo_kaos import API_KEY, PUBLIC_PEM, Clock

    clock = Clock()
    fake = FakeBinance(clock_ms=clock.ms, public_pem=PUBLIC_PEM, api_key=API_KEY)
    trader = _trader(fake, clock)
    state = trader.verify_permissions()
    assert not state.tamam and any("sabit IP" in item for item in state.engeller)
    fake.restrictions["ipRestrict"] = True
    assert trader.verify_permissions().tamam


# --- gözcü --------------------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FakeOpener:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes | None]] = []
        self.error: Exception | None = None

    def __call__(self, request, timeout):
        self.requests.append((request.get_method(), request.full_url, request.data))
        if self.error is not None:
            raise self.error
        return FakeResponse(b"OK")


def test_gozcu_adresi_denetlenir():
    assert valid_url(URL)
    assert valid_url(URL + "  ")  # yapıştırırken gelen boşluk kırpılır
    assert valid_url("https://hc-ping.com/pingkey123/albsat-sunucu")
    for bad in ("http://hc-ping.com/1f2e3d4c-aaaa", "hc-ping.com/1f2e3d4c-aaaa-bbbb",
                "https://user:pw@hc-ping.com/1f2e3d4c-aaaa", URL + "?x=1", "",
                "https://hc-ping.com/kisa"):
        assert not valid_url(bad), bad
    with pytest.raises(WatchdogError):
        PingClient(SecretText("http://hc-ping.com/1f2e3d4c-aaaa-bbbb"))


def test_ping_istemcisi_istekleri_ve_hata_metninde_adres_yok():
    opener = FakeOpener()
    client = PingClient(SecretText(URL), opener=opener)
    client.ok()
    client.fail("canlı döngü takıldı")
    client.log("kapatıldı")
    assert [(m, u) for m, u, _ in opener.requests] == [
        ("GET", URL), ("POST", URL + "/fail"), ("POST", URL + "/log")]
    assert opener.requests[1][2] == "canlı döngü takıldı".encode()
    assert repr(client) == "PingClient(hc-ping.com)"

    opener.error = urllib.error.URLError(f"bağlanılamadı: {URL}")
    with pytest.raises(WatchdogError) as caught:
        client.ok()
    assert URL not in str(caught.value) and "hc-ping.com" in str(caught.value)
    opener.error = urllib.error.HTTPError(URL, 404, "not found", {}, None)  # type: ignore[arg-type]
    with pytest.raises(WatchdogError, match="HTTP 404") as caught:
        client.ok()
    assert URL not in str(caught.value)


def test_gozcu_iki_kotu_okumada_alarm_verir_duzelince_haber_verir():
    opener = FakeOpener()
    notifier = MemoryNotifier()
    verdicts = [HealthVerdict(True, "sağlıklı")]
    watchdog = Watchdog(PingClient(SecretText(URL), opener=opener),
                        check=lambda: verdicts[-1], notifier=notifier, clock=lambda: T0)
    assert watchdog.beat() == "ok"
    verdicts.append(HealthVerdict(False, "piyasa verisi 5 dakikadan eski: BTCUSDT"))
    assert watchdog.beat() == "bekliyor"
    assert opener.requests[-1][0] == "POST" and opener.requests[-1][1] == URL
    assert FAILS_BEFORE_ALARM == 2
    assert watchdog.beat() == "fail"
    assert opener.requests[-1][1] == URL + "/fail"
    assert watchdog.beat() == "fail"
    alarms = [n.metin for n in notifier.recent() if "Gözcü" in n.metin]
    assert len(alarms) == 1 and "BTCUSDT" in alarms[0]
    verdicts.append(HealthVerdict(True, "sağlıklı"))
    assert watchdog.beat() == "ok"
    assert "giderildi" in notifier.recent(1)[0].metin
    status = watchdog.status()
    assert status["sunucu"] == "hc-ping.com" and status["ping_sayisi"] == 5
    assert URL not in json.dumps(status)


def test_gozcu_denetimi_patlarsa_sorun_sayilir_ulasilamazsa_yazilir():
    opener = FakeOpener()

    def broken() -> HealthVerdict:
        raise RuntimeError("beklenmedik")

    watchdog = Watchdog(PingClient(SecretText(URL), opener=opener), check=broken)
    watchdog.beat()
    assert watchdog.beat() == "fail"
    opener.error = OSError("ağ yok")
    watchdog.beat()
    assert "ulaşılamadı" in (watchdog.status()["son_hata"] or "")


def test_gozcu_kapanista_not_birakir():
    opener = FakeOpener()
    watchdog = Watchdog(PingClient(SecretText(URL), opener=opener),
                        check=lambda: HealthVerdict(True, "sağlıklı"), interval=3600)
    watchdog.start()
    watchdog.stop(note="Uygulama kapatıldı")
    assert opener.requests[0] == ("GET", URL, None)
    assert opener.requests[-1][1] == URL + "/log"


# --- çalışma zamanı: sağlık kararı ve arayüz -------------------------------------------------


class FakeRunner:
    def __init__(self, age: float | None = 1.0, stale: list[str] | None = None) -> None:
        self.age = age
        self.stale = stale or []

    def loop_age(self) -> float | None:
        return self.age

    def stale_symbols(self, max_age: float) -> list[str]:
        return self.stale

    def stop(self) -> None:
        pass


def _runtime(tmp_path: Path) -> Runtime:
    return Runtime.build(tmp_path, symbols=("BTCUSDT",), periods=("15m",), online=False,
                         use_telegram=False)


def test_saglik_karari_dongu_veri_ve_disk(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    assert runtime.health_verdict() == HealthVerdict(True, "sağlıklı")
    runtime.runner = FakeRunner(age=400.0, stale=["BTCUSDT"])  # type: ignore[assignment]
    verdict = runtime.health_verdict()
    assert not verdict.saglikli
    assert "400 sn" in verdict.neden and "BTCUSDT" in verdict.neden

    class Usage:
        free = MIN_FREE_BYTES - 1

    runtime.runner = FakeRunner()  # type: ignore[assignment]
    monkeypatch.setattr("albsat.api.runtime.shutil.disk_usage", lambda path: Usage)
    assert "boş yer" in runtime.health_verdict().neden
    runtime.runner = None


def test_cevrimdisi_calismada_gozcu_kapali_yedek_acik_ve_arayuzde_gorunur(tmp_path):
    runtime = _runtime(tmp_path)
    assert runtime.watchdog is None and runtime.backups is not None
    client = TestClient(create_app(AppState(veri_dizini=tmp_path, runtime=runtime)),
                        base_url=BASE)
    veri = client.get("/api/kagit/durum").json()
    assert veri["gozcu"]["kurulu"] is False and "kapalı" in veri["gozcu"]["aciklama"]
    assert veri["yedek"]["yedek_sayisi"] == 0


def test_gozcu_kurulu_degilse_nasil_kurulacagi_soylenir(tmp_path, monkeypatch, sir_dizini):
    from albsat.api import runtime as runtime_module

    runtime = _runtime(tmp_path)
    watchdog, note = runtime_module._watchdog(runtime)
    assert watchdog is None and "bash kurulum.sh gozcu" in note
    keychain.write("albsat-gozcu", "ping-adresi", SecretText(URL))
    watchdog, note = runtime_module._watchdog(runtime)
    assert watchdog is not None and "hc-ping.com" in note and URL not in note


def test_calisma_zamani_yedegi_ve_gozcuyu_baslatir_durdurur(tmp_path):
    runtime = _runtime(tmp_path)
    opener = FakeOpener()
    runtime.watchdog = Watchdog(PingClient(SecretText(URL), opener=opener),
                                check=runtime.health_verdict, interval=3600)
    runtime.start()
    runtime.stop()
    assert opener.requests[0][1] == URL
    assert opener.requests[-1][1] == URL + "/log"


# --- dongu yaşı ------------------------------------------------------------------------------


def test_dongu_yasi_acilis_suresince_bos_sonra_olculur(tmp_path):
    from test_canli_dongu import WallClock, make_runner

    from albsat.paper import runner as runner_module

    clock = WallClock(T0)
    runner, _, market, _, _ = make_runner(tmp_path, clock)
    mono = [100.0]
    runner.monotonic = lambda: mono[0]
    assert runner.loop_age() is None and runner.stale_symbols(300) == []
    runner._started_mono = 100.0
    runner._loop_mono = 101.0
    mono[0] = 102.0
    assert runner.stale_symbols(300) == []  # akış yeni bağlanıyor, henüz fiyat yok
    runner._loop_mono = None
    mono[0] = 100.0 + runner_module.STARTUP_GRACE_SECONDS - 1
    assert runner.loop_age() is None  # açılış uzlaştırması sürüyor
    mono[0] = 100.0 + runner_module.STARTUP_GRACE_SECONDS + 1
    assert runner.loop_age() == runner_module.STARTUP_GRACE_SECONDS + 1
    runner._loop_mono = mono[0]
    mono[0] += 5
    assert runner.loop_age() == 5
    assert runner.stale_symbols(300) == ["BTCUSDT"]  # hiç fiyat gelmedi
    from decimal import Decimal

    market.on_book("BTCUSDT", Decimal("100"), Decimal("101"))
    assert runner.stale_symbols(300) == []
    clock.advance(seconds=301)
    assert runner.stale_symbols(300) == ["BTCUSDT"]


# --- tek kopya kilidi -------------------------------------------------------------------------


def test_ikinci_kopya_acilmaz_kilit_birakilinca_acilir(tmp_path):
    first = InstanceLock(tmp_path)
    first.acquire()
    try:
        assert "süreç" in (held_by_other(tmp_path) or "")
        with pytest.raises(AlreadyRunning, match="zaten çalışıyor"):
            InstanceLock(tmp_path).acquire()
    finally:
        first.release()
    assert held_by_other(tmp_path) is None
    with InstanceLock(tmp_path):
        pass


def test_arayuz_ikinci_kez_acilmaz(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(serve_cli.Runtime, "build",
                        lambda *a, **k: pytest.fail("ikinci kopya kurulmamalı"))
    with InstanceLock(tmp_path):
        code = serve_cli.main(["--veri-dizini", str(tmp_path), "--cevrimdisi",
                               "--tarayici-acma"])
    assert code == 1
    assert "zaten çalışıyor" in capsys.readouterr().err



def test_sunucuda_durdurma_sinyali_duzgun_kapatir(tmp_path):
    # "bash kurulum.sh durdur" SIGTERM gönderir. uvicorn sinyali yeniden
    # yükselttiği için süreç eskiden kapanış adımları çalışmadan ölüyordu:
    # gözcüye not gitmiyor, canlı yürütücü durdurulmuyordu.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "albsat.cli.serve", "--veri-dizini", str(tmp_path),
         "--cevrimdisi", "--tarayici-acma", "--port", str(port), "--sabit-port"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/saglik", timeout=2)
                break
            except OSError:
                time.sleep(0.3)
        else:
            pytest.fail("arayüz açılmadı")
        assert held_by_other(tmp_path) is not None
        process.send_signal(signal.SIGTERM)
        out, _ = process.communicate(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, out
    assert "Kapatılıyor..." in out and "Kapatıldı." in out
    assert held_by_other(tmp_path) is None

# --- gözcü komutu -------------------------------------------------------------------------


def test_gozcu_komutu_adresi_denedikten_sonra_kaydeder(sir_dizini, monkeypatch, capsys):
    opener = FakeOpener()
    monkeypatch.setattr(gozcu_cli, "PingClient",
                        lambda url: PingClient(url, opener=opener))
    monkeypatch.setattr("builtins.input", lambda prompt: URL)
    assert gozcu_cli.main([]) == 0
    assert keychain.read("albsat-gozcu", "ping-adresi").reveal() == URL
    out = capsys.readouterr().out
    assert URL not in out and "sunucunun sır dizinine" in out

    monkeypatch.setattr(gozcu_cli.time, "sleep", lambda seconds: None)
    assert gozcu_cli.main(["--sina"]) == 0
    assert [u for _, u, _ in opener.requests][-2:] == [URL + "/fail", URL]
    assert gozcu_cli.main(["--sil"]) == 0
    assert keychain.read("albsat-gozcu", "ping-adresi") is None


def test_gozcu_komutu_ulasamazsa_kaydetmez(sir_dizini, monkeypatch, capsys):
    opener = FakeOpener()
    opener.error = urllib.error.HTTPError(URL, 404, "not found", {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(gozcu_cli, "PingClient", lambda url: PingClient(url, opener=opener))
    monkeypatch.setattr("builtins.input", lambda prompt: URL)
    assert gozcu_cli.main([]) == 1
    assert keychain.read("albsat-gozcu", "ping-adresi") is None
    monkeypatch.setattr("builtins.input", lambda prompt: "hc-ping.com/yanlis")
    assert gozcu_cli.main([]) == 1
    assert "benzemiyor" in capsys.readouterr().out


def test_saglik_ucu_karari_verir_denetim_bozuksa_dusmez(tmp_path):
    runtime = _runtime(tmp_path)
    client = TestClient(create_app(AppState(veri_dizini=tmp_path, runtime=runtime)),
                        base_url=BASE)
    assert client.get("/api/saglik").json()["calisma"] == {"saglikli": True,
                                                            "neden": "sağlıklı"}
    runtime.runner = FakeRunner(age=999.0)  # type: ignore[assignment]
    assert client.get("/api/saglik").json()["calisma"]["saglikli"] is False
    runtime.runner = object()  # type: ignore[assignment]
    veri = client.get("/api/saglik").json()["calisma"]
    assert veri["saglikli"] is False and "AttributeError" in veri["neden"]
    runtime.runner = None


def test_yedek_adi_yalnizca_dosya_adiyla_verilebilir(tmp_path, capsys):
    _database(tmp_path)
    archive = backup.create(tmp_path, now=T0).yol
    assert yedek_cli.main(["--veri-dizini", str(tmp_path), "--sina", archive.name]) == 0
    assert yedek_cli.main(["--veri-dizini", str(tmp_path), "--sina", "yok.tar.gz"]) == 1


def test_canli_sinamasi_uygulama_acikken_emir_gondermez(tmp_path, monkeypatch, capsys):
    from albsat.cli import canli as canli_cli

    monkeypatch.setattr(canli_cli.keychain, "available", lambda: True)
    monkeypatch.setattr(canli_cli, "load_key", lambda service: object())
    monkeypatch.setattr(canli_cli, "LiveTrader", lambda *a, **k: object())
    monkeypatch.setattr(canli_cli, "smoke",
                        lambda *a, **k: pytest.fail("uygulama açıkken sınama emri gitmemeli"))
    with InstanceLock(tmp_path):
        code = canli_cli.main(["--veri-dizini", str(tmp_path), "--sina"])
    assert code == 1
    out = capsys.readouterr().out
    assert "Sınama emri gönderilmedi" in out and "Control-C" in out
