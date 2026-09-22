"""Arayüzü başlatan komut (Faz 3).

Kullanım (Mac'te, sanal ortam etkinken)::

    python -m albsat.cli.serve

Sunucu **yalnızca 127.0.0.1'e** bağlanır (SPEC.md §5: "Arayüz Mac'te sadece
127.0.0.1'e bağlansın"). Dinlenecek adres bilerek seçenek olarak sunulmuyor:
bir bayrakla 0.0.0.0'a açılabilen yerel arayüz, er ya da geç açılır.

Sunucu internete çıkmaz ve API anahtarına erişmez. Veriyi ``--veri-dizini``
altındaki dosyalardan okur; tazeleme ayrı bir adımdır.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from albsat.api.app import MODE_TR, AppState, asset_version, create_app
from albsat.strategy import rules as rulestore

#: Yalnızca yerel arayüz. Değiştirilebilir bir seçenek değildir.
HOST = "127.0.0.1"
DEFAULT_PORT = 8756


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="albsat-arayuz",
        description="Sadece Öneri arayüzünü yerel olarak başlatır (Faz 3)",
    )
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--butce", default="100", help="Bot bütçesi, USDT (varsayılan 100)"
    )
    parser.add_argument(
        "--risk",
        default="1.0",
        help="İşlem başına risk, bütçenin yüzdesi (varsayılan 1.0)",
    )
    parser.add_argument("--semboller", nargs="+", default=["BTCUSDT", "SOLUSDT"])
    parser.add_argument("--periyotlar", nargs="+", default=["15m", "1h"])
    parser.add_argument(
        "--tarayici-acma",
        action="store_true",
        help="Tarayıcıyı kendiliğinden açma",
    )
    return parser


def _free_port(port: int, attempts: int = 20) -> int:
    """İstenen port doluysa bir sonrakini dener.

    Kullanıcıya "adres kullanımda" hatası göstermek yerine çalışan bir port
    bulmak, terminale alışık olmayan biri için çok daha iyidir; hangi portun
    seçildiği ekrana yazılır.
    """
    for offset in range(attempts):
        candidate = port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((HOST, candidate))
            except OSError:
                continue
            return candidate
    return port


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    state = AppState(
        veri_dizini=Path(args.veri_dizini),
        butce_usdt=str(args.butce),
        islem_basi_risk_yuzde=str(args.risk),
        semboller=tuple(args.semboller),
        periyotlar=tuple(args.periyotlar),
    )

    print("Arayüz hazırlanıyor...", flush=True)
    print(f"  Veri dizini    : {state.veri_dizini.resolve()}", flush=True)

    rule_path = rulestore.path_for(state.veri_dizini)
    if rule_path.exists():
        try:
            ruleset = rulestore.load(rule_path)
        except rulestore.RuleStoreError as error:
            print(f"  ! Kural deposu okunamadı: {error}", file=sys.stderr, flush=True)
        else:
            print(
                f"  Kural deposu   : {ruleset.kabul_edilen_sayisi} kabul edilen "
                f"kural, {len(ruleset.incelenen_adaylar)} incelenen aday "
                f"({ruleset.kosu.kosu_zamani_utc})",
                flush=True,
            )
            if ruleset.kabul_edilen_sayisi == 0:
                print(
                    "                   Kabul edilen kural yok; arayüz "
                    "'önerilecek kural yok' diyecek ve nedenini gösterecek. "
                    "Bu beklenen durumdur.",
                    flush=True,
                )
    else:
        # Kural deposu Faz 3'te eklendi; Faz 2'yi daha önce çalıştırmış
        # kullanıcıda bu dosya yok. İki seçenek de yazılıyor: "tarama"
        # yalnızca depoyu üretir, seçeneksiz çalıştırma borsa filtrelerini
        # de indirir. Yalnızca birini yazmak, eksik kalan öbürünü
        # kullanıcının kendi başına bulmasını gerektiriyordu.
        print(
            f"  ! Kural deposu bulunamadı: {rule_path}\n"
            "    Örüntü taramasını çalıştırın:\n"
            "      bash kurulum.sh tarama   (internete çıkmaz, yalnızca tarama)\n"
            "      bash kurulum.sh          (veriyi ve borsa filtrelerini de tazeler)",
            flush=True,
        )

    port = _free_port(int(args.port))
    address = f"http://{HOST}:{port}/"
    print(f"  Mod            : {MODE_TR} (emir gönderilmez)", flush=True)
    # Aynı kimlik sayfanın en altında da yazar; ikisi farklıysa tarayıcı
    # eski bir sekmeyi gösteriyordur.
    print(f"  Arayüz sürümü  : {asset_version()}", flush=True)
    print(f"  Adres          : {address}", flush=True)
    print("\nDurdurmak için bu pencerede Control-C tuşlayın.\n", flush=True)

    if not args.tarayici_acma:
        # Sunucu ayağa kalkmadan açılan sekme boş sayfa gösterir; bir saniye
        # gecikme kullanıcının "çalışmadı" sanmasını önlüyor.
        threading.Timer(1.5, lambda: webbrowser.open(address)).start()

    import uvicorn

    uvicorn.run(
        create_app(state),
        host=HOST,
        port=port,
        log_level="warning",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
