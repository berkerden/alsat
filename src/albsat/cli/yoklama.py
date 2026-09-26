"""Uygulama çalışıyor ve sağlıklı mı? Docker sağlık denetimi ve ``durum`` için.

Yalnızca bu bilgisayardaki arayüz sunucusuna (127.0.0.1) sorar; internete
çıkmaz. Çıkış kodu 0: sunucu yanıt verdi ve canlı döngü sağlıklı; 1: değil.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from albsat.cli.serve import DEFAULT_PORT, HOST
from albsat.core.clock import istanbul_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="albsat-yoklama",
                                     description="Yerel arayüz sunucusunun sağlığını sorar")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--sessiz", action="store_true", help="Yalnızca çıkış kodu")
    args = parser.parse_args(argv)
    url = f"http://{HOST}:{args.port}/api/saglik"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        if not args.sessiz:
            print(f"Uygulama yanıt vermiyor ({HOST}:{args.port}): {error}")
        return 1
    verdict = payload.get("calisma") or {}
    healthy = bool(verdict.get("saglikli"))
    if not args.sessiz:
        print(("✓ Uygulama çalışıyor: " if healthy else "! Uygulama yanıt veriyor ama: ")
              + str(verdict.get("neden", "bilinmiyor")))
        print(f"  Emir yetkisi : {payload.get('emir_yetkisi', '?')}")
        watchdog = payload.get("gozcu") or {}
        if watchdog.get("kurulu"):
            state = {"ok": "çalışıyor", "fail": "SORUN VAR bildirildi",
                     "bekliyor": "uyarı (bir kez daha görülürse alarm)"}.get(
                str(watchdog.get("son_durum")), "henüz ping yok")
            last = istanbul_text(watchdog.get("son_ping_utc")) or "—"
            print(f"  Gözcü        : {watchdog.get('sunucu')} · {state} · son ping {last}")
            if watchdog.get("son_hata"):
                print(f"                 ! {watchdog['son_hata']}")
        else:
            print(f"  Gözcü        : {watchdog.get('aciklama') or 'kurulu değil'}")
        backups = payload.get("yedek") or {}
        if backups:
            last = istanbul_text(backups.get("son_yedek_utc")) or "henüz yok"
            print(f"  Son yedek    : {last} ({backups.get('yedek_sayisi', 0)} yedek)")
            if backups.get("son_hata"):
                print(f"                 ! {backups['son_hata']}")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
