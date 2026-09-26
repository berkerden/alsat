"""Depoya sır girmesin diye gizli bilgi taraması (SPEC §5, Faz 7).

İki yerden çalışır:

* ``.githooks/pre-commit``: kaydedilecek (staged) dosyaların kaydedilecek
  hâlini tarar; bulursa kaydı durdurur. Etkinleştirmek için depoda bir kez
  ``git config core.hooksPath .githooks``.
* ``tests/test_gizli_tarama.py``: depodaki bütün dosyaları tarar. Testler her
  kurulumda ve Docker derlemesinde çalıştığı için kanca etkin olmasa da sır
  içeren bir depo yakalanır.

Yalnızca standart kütüphane kullanır: kanca sanal ortam olmadan da çalışır.
Bulgu yazarken sırrın kendisini yazmaz, yalnızca dosya, satır ve türü.

Bilerek sahte bir örnek (test verisi) içeren satır, aynı satıra
``gizli-tarama: sahte`` yazılarak taramadan çıkarılır.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ALLOW_MARKER = "gizli-tarama: sahte"
MAX_BYTES = 2_000_000
# Depoda olmayan ya da taranması anlamsız dizinler (ağaç taranırken).
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "veri", "data", "arsiv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules",
})

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("özel anahtar (PEM)", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("Telegram bot jetonu",
     re.compile(r"(?<![0-9])[0-9]{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    ("gözcü (Healthchecks) adresi",
     re.compile(r"hc-ping\.com/(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[A-Za-z0-9_-]{22}/)")),
    # Binance API ve HMAC anahtarları 64 karakter, büyük-küçük harf ve rakam
    # karışık. Yalnızca küçük harfli onaltılık diziler (SHA-256 özetleri,
    # kilit dosyası) bu yüzden sayılmaz.
    ("Binance anahtarı", re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])"
                                    r"(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])")),
)


@dataclass(frozen=True)
class Finding:
    yol: str
    satir: int
    tur: str

    def text(self) -> str:
        return f"{self.yol}:{self.satir}: {self.tur}"


def scan_text(text: str, name: str = "") -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for kind, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(Finding(name, number, kind))
    return findings


def scan_bytes(data: bytes, name: str) -> list[Finding]:
    if len(data) > MAX_BYTES or b"\x00" in data[:8192]:
        return []  # ikili ya da çok büyük dosya: metin sırrı taşımaz sayılır
    return scan_text(data.decode("utf-8", errors="replace"), name)


def tree_files(root: Path) -> list[Path]:
    """Git yoksa (örneğin Docker derlemesinde) taranacak dosyalar."""
    result: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        result.extend(Path(folder) / name for name in sorted(files))
    return result


def tracked_files(root: Path) -> list[Path] | None:
    """Git'in izlediği dosyalar; ``root`` bir git deposu değilse ``None``."""
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True,
                             capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [root / item for item in out.decode().split("\0") if item]


def scan_files(paths: Iterable[Path], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            name = str(path.relative_to(root))
        except ValueError:
            name = str(path)
        findings.extend(scan_bytes(path.read_bytes(), name))
    return findings


def scan_staged(root: Path) -> list[Finding]:
    """Kaydedilecek dosyaların dizindeki (index) hâlini tarar."""
    names = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        check=True, capture_output=True,
    ).stdout.decode().split("\0")
    findings: list[Finding] = []
    for name in filter(None, names):
        data = subprocess.run(["git", "-C", str(root), "show", f":{name}"], check=True,
                              capture_output=True).stdout
        findings.extend(scan_bytes(data, name))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Depoda sır var mı diye tarar.")
    parser.add_argument("--staged", action="store_true",
                        help="Yalnızca kaydedilecek (git add) dosyalar (pre-commit kancası).")
    parser.add_argument("--kok", default=".", help="Depo kökü (varsayılan: bulunulan dizin).")
    parser.add_argument("dosyalar", nargs="*", type=Path)
    args = parser.parse_args(argv)
    root = Path(args.kok).resolve()

    if args.staged:
        findings = scan_staged(root)
    elif args.dosyalar:
        findings = scan_files(args.dosyalar, root)
    else:
        findings = scan_files(tracked_files(root) or tree_files(root), root)

    if not findings:
        return 0
    print("Sır olabilecek bilgi bulundu; kayıt durduruldu:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.text()}", file=sys.stderr)
    print(
        "Gerçek bir sırsa satırı silin; sır hiçbir zaman depoya girmez.\n"
        f"Bilerek yazılmış sahte test verisiyse aynı satıra '{ALLOW_MARKER}' yazın.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
