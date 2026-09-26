"""Gizli bilgi taraması (SPEC §5, Faz 7).

Örnek sırlar parçalardan çalışma anında kurulur; bu dosyanın kendisi
taramaya takılmasın diye.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from albsat.core import secretscan

ROOT = Path(__file__).resolve().parents[1]
BINANCE = "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zv" + "sw0MuIgwCIPy6utIco14y7Ju91duEh8A"
TELEGRAM = "1234567890" + ":" + "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"
PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
PING = "https://hc-ping.com/" + "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


@pytest.mark.parametrize(("text", "kind"), [
    (f"api_key = '{BINANCE}'", "Binance anahtarı"),
    (f"TOKEN={TELEGRAM}", "Telegram bot jetonu"),
    (PEM, "özel anahtar (PEM)"),
    (f"curl {PING}", "gözcü (Healthchecks) adresi"),
])
def test_sir_turleri_bulunur(text, kind):
    findings = secretscan.scan_text(f"birinci satır\n{text}\n", "a.py")
    assert [(f.satir, f.tur) for f in findings] == [(2, kind)]


def test_ozetler_ve_siradan_metin_sir_sayilmaz():
    ozet = "a3" * 32  # kilit dosyasındaki SHA-256 özeti: küçük harfli onaltılık
    metin = (f"--hash=sha256:{ozet}\n"
             "python:3.12-slim@sha256:" + "f7" * 32 + "\n"
             "saat 12345678:30 değil\n"
             "https://hc-ping.com/<sizin-kodunuz>\n")
    assert secretscan.scan_text(metin) == []


def test_sahte_isaretli_satir_atlanir():
    assert secretscan.scan_text(f"x = '{TELEGRAM}'  # {secretscan.ALLOW_MARKER}") == []


def test_bulgu_sirrin_kendisini_yazmaz(tmp_path, capsys):
    dosya = tmp_path / "ayar.txt"
    dosya.write_text(f"jeton: {TELEGRAM}\n")
    assert secretscan.main(["--kok", str(tmp_path), str(dosya)]) == 1
    err = capsys.readouterr().err
    assert "ayar.txt:1: Telegram bot jetonu" in err
    assert TELEGRAM not in err and TELEGRAM.split(":")[1] not in err


def test_ikili_dosya_taranmaz(tmp_path):
    (tmp_path / "resim.png").write_bytes(b"\x89PNG\x00\x00" + TELEGRAM.encode())
    assert secretscan.scan_files([tmp_path / "resim.png"], tmp_path) == []


def test_depoda_sir_yok():
    # Kanca etkin olmasa da (Berk'in Mac'i, Docker derlemesi) depoya giren sır
    # burada yakalanır. Git yoksa dizin ağacı taranır.
    files = secretscan.tracked_files(ROOT) or secretscan.tree_files(ROOT)
    assert len(files) > 50
    findings = secretscan.scan_files(files, ROOT)
    assert findings == [], "\n".join(f.text() for f in findings)


@pytest.mark.skipif(shutil.which("git") is None, reason="git kurulu değil")
def test_kanca_kaydedilecek_dosyada_sir_varsa_durdurur(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "temiz.py").write_text("x = 1\n")
    git("add", "temiz.py")
    assert secretscan.main(["--staged", "--kok", str(tmp_path)]) == 0
    (tmp_path / "ayar.py").write_text(f"KEY = '{BINANCE}'\n")
    git("add", "ayar.py")
    assert secretscan.main(["--staged", "--kok", str(tmp_path)]) == 1
    # Dizindeki (index) hâl taranır: dosya diskte düzeltilip yeniden eklenince geçer.
    (tmp_path / "ayar.py").write_text("KEY = None\n")
    git("add", "ayar.py")
    assert secretscan.main(["--staged", "--kok", str(tmp_path)]) == 0


def test_kanca_dosyasi_calistirilabilir_ve_tarayiciyi_cagirir():
    hook = ROOT / ".githooks" / "pre-commit"
    if not hook.exists():
        pytest.skip("kanca bu kopyada yok (Docker derlemesi)")
    assert hook.stat().st_mode & 0o111
    assert "albsat.core.secretscan --staged" in hook.read_text()
