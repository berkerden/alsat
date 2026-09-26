"""Yedek alma, listeleme ve geri yükleme komutu.

Kullanım (Mac'te)::

    bash kurulum.sh yedek              # şimdi yedek al
    bash kurulum.sh yedek --listele    # yedekleri göster

Uygulama açıkken günde bir yedek zaten kendiliğinden alınır
(``core.backup.BackupScheduler``); bu komut elle almak ve geri yüklemek
içindir. Geri yükleme yalnızca uygulama kapalıyken yapılır ve mevcut
dosyaları silmez, kenara taşır.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from albsat.cli.serve import DEFAULT_PORT
from albsat.core import backup, keychain
from albsat.core.clock import istanbul_text
from albsat.core.instance import held_by_other


def _say(text: str = "") -> None:
    print(text, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="albsat-yedek",
                                     description="Veritabanı yedeği alır ya da geri yükler")
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="Uygulamanın açık olup olmadığını anlamak için bakılan port")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--listele", action="store_true", help="Yedekleri listele")
    group.add_argument("--geri-yukle", metavar="DOSYA", type=Path,
                       help="Bu yedeği geri yükle (uygulama kapalıyken)")
    group.add_argument("--sina", metavar="DOSYA", type=Path,
                       help="Yedeği açmadan sına (özetler ve bütünlük)")
    return parser


def _size(value: int) -> str:
    if value >= 1 << 20:
        return f"{value / (1 << 20):.1f} MB"
    return f"{value / 1024:.0f} KB"


def list_all(root: Path) -> int:
    items = backup.list_backups(root)
    if not items:
        _say(f"Yedek yok ({backup.directory(root)}).")
        return 0
    _say(f"{len(items)} yedek, en yenisi başta ({backup.directory(root).resolve()}):")
    for item in items:
        moment = backup.backup_time(item)
        _say(f"  {item.name}   {istanbul_text(moment)}   {_size(item.stat().st_size)}")
    return 0


def take(root: Path) -> int:
    _say("Yedek alınıyor...")
    try:
        result = backup.create(root)
    except backup.BackupError as error:
        _say(f"! {error}")
        return 1
    _say(f"✓ Yedek alındı: {result.yol.resolve()} ({_size(result.boyut_bayt)})")
    _say(f"  İçindekiler: {', '.join(result.dosyalar)}")
    _say(f"  Son {backup.KEEP} yedek tutulur; eskiler silinir.")
    return 0


def check(path: Path) -> int:
    try:
        manifest = backup.verify(path)
    except backup.BackupError as error:
        _say(f"! {error}")
        return 1
    _say(f"✓ Yedek sağlam: {path.name} ({istanbul_text(manifest.get('zaman_utc'))})")
    for name in manifest["dosyalar"]:
        _say(f"  {name}")
    return 0


def restore(root: Path, path: Path, port: int) -> int:
    _say(f"Geri yükleniyor: {path}")
    try:
        result = backup.restore(
            root, path,
            running=lambda: held_by_other(root) is not None or backup.app_running(port),
        )
    except backup.BackupError as error:
        _say(f"! {error}")
        if keychain.secret_directory() is not None and "çalışıyor" in str(error):
            _say("  Sunucuda durdurmak için: bash kurulum.sh durdur")
        return 1
    _say(f"✓ Geri yüklendi: {', '.join(result.dosyalar)}")
    _say(f"  Önceki dosyalar silinmedi, şuraya taşındı: {result.eskiler.resolve()}")
    if keychain.secret_directory() is not None:
        _say("  Uygulamayı başlatmak için: bash kurulum.sh baslat")
        _say("  Açılışta borsayla uzlaştırılır.")
    else:
        _say("  Uygulamayı yeniden açabilirsiniz; açılışta borsayla uzlaştırılır.")
    return 0


def _resolve(root: Path, path: Path) -> Path:
    """Yalnızca dosya adı verildiyse yedek dizininde arar (listede görünen ad yeter)."""
    if path.exists():
        return path
    candidate = backup.directory(root) / path.name
    return candidate if candidate.exists() else path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.veri_dizini)
    if args.listele:
        return list_all(root)
    if args.sina is not None:
        return check(_resolve(root, args.sina))
    if args.geri_yukle is not None:
        return restore(root, _resolve(root, args.geri_yukle), args.port)
    return take(root)


if __name__ == "__main__":
    sys.exit(main())
