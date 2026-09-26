"""Arayüzü başlatan komut.

Kullanım (Mac'te)::

    bash kurulum.sh arayuz

Sunucu **yalnızca 127.0.0.1'e** bağlanır (SPEC.md §5: "Arayüz Mac'te sadece
127.0.0.1'e bağlansın"). Dinlenecek adres bilerek seçenek olarak sunulmuyor:
bir bayrakla 0.0.0.0'a açılabilen yerel arayüz, er ya da geç açılır.

Faz 4'ten beri sunucu açılınca Binance'in **genel** piyasa verisine bağlanır
(WebSocket; koparsa REST ile yedek yoklama). Bu veri hesap bilgisi içermez
ve API anahtarı gerektirmez. Kâğıt işlem bu veriyle çalışır. Telegram
kuruluysa bildirimler telefona gider. ``--cevrimdisi`` ile açılırsa
internete hiç çıkmaz; o zaman kâğıt işlem de çalışmaz.

Faz 5'ten beri Demo Mode anahtarı (``bash kurulum.sh demo-anahtar``)
kuruluysa Demo işlem sekmesi Binance **Demo Mode**'a (sahte para) emir
gönderir. Faz 6'dan beri canlı işlem anahtarı (``bash kurulum.sh
canli-anahtar``) kuruluysa Canlı işlem sekmesi Binance **canlı hesaba
(gerçek para)** emir gönderebilir; yalnızca Yarı/Tam Otomatik'e elle alınmış
coinler için, emir başına tavanla ve anahtarın para çekme izni kapalı
okunduktan sonra.

Her açılışta bütün coinler "Sadece Öneri" modunda başlar (SPEC §2).

Faz 7'den beri aynı veri dizininde ikinci bir kopya açılmaz
(``core.instance``); günde bir yedek alınır (``core.backup``) ve gözcü
kuruluysa uygulama kapanınca ya da takılınca alarm gelir (``notify.gozcu``).
Sunucuda (Docker) ``--sabit-port`` ile çalışır: port doluysa bir sonrakine
kaymaz, hata verir; gözcü ve sağlık denetimi hep aynı porta bakar.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from albsat.api.app import MODE_TR, AppState, asset_version, create_app
from albsat.api.runtime import Runtime, attach_telegram
from albsat.core import backup, keychain
from albsat.core.instance import AlreadyRunning, InstanceLock
from albsat.core.tls import enable_system_trust
from albsat.strategy import rules as rulestore

#: Yalnızca yerel arayüz. Değiştirilebilir bir seçenek değildir.
HOST = "127.0.0.1"
DEFAULT_PORT = 8756


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="albsat-arayuz",
        description="Arayüzü yerel olarak başlatır (yalnızca 127.0.0.1)",
    )
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--butce", default="100",
        help="Kâğıt motoru yokken kartlarda kullanılan bütçe; normalde risk limitlerinden "
        "okunur",
    )
    parser.add_argument(
        "--risk",
        default="1.0",
        help="Kâğıt motoru yokken kartlarda kullanılan işlem başı risk yüzdesi",
    )
    parser.add_argument(
        "--cevrimdisi",
        action="store_true",
        help="Canlı piyasa verisine bağlanma (internete çıkmaz; kâğıt işlem çalışmaz)",
    )
    parser.add_argument("--semboller", nargs="+", default=["BTCUSDT", "SOLUSDT"])
    parser.add_argument("--periyotlar", nargs="+", default=["15m", "1h"])
    parser.add_argument(
        "--tarayici-acma",
        action="store_true",
        help="Tarayıcıyı kendiliğinden açma",
    )
    parser.add_argument(
        "--sabit-port",
        action="store_true",
        help="Port doluysa bir sonrakine kayma, hata ver (sunucuda)",
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
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state = AppState(
        veri_dizini=Path(args.veri_dizini),
        butce_usdt=str(args.butce),
        islem_basi_risk_yuzde=str(args.risk),
        semboller=tuple(args.semboller),
        periyotlar=tuple(args.periyotlar),
    )

    print("Arayüz hazırlanıyor...", flush=True)
    print(f"  Veri dizini    : {state.veri_dizini.resolve()}", flush=True)

    lock = InstanceLock(state.veri_dizini)
    try:
        lock.acquire()
    except AlreadyRunning as error:
        print(f"\n! {error}", file=sys.stderr, flush=True)
        print(
            "  Açık olan kopyayı kullanmak için tarayıcıda şu adresi açın:\n"
            f"    http://{HOST}:{args.port}/\n"
            "  Kapatmak için o kopyanın çalıştığı Terminal penceresinde Control-C tuşlayın.",
            file=sys.stderr, flush=True,
        )
        return 1

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

    online = not args.cevrimdisi
    if online:
        # Kullanıcının ağında HTTPS'i yeniden imzalayan bir katman var; macOS
        # güven deposu kullanılır. Doğrulama hiçbir koşulda kapatılmaz.
        enable_system_trust()
    runtime = Runtime.build(
        state.veri_dizini, symbols=state.semboller, periods=state.periyotlar, online=online
    )
    attach_telegram(runtime)
    state.runtime = runtime

    port = int(args.port) if args.sabit_port else _free_port(int(args.port))
    address = f"http://{HOST}:{port}/"
    print(f"  Mod            : bütün coinler {MODE_TR} (canlı mod elle seçilmeden canlı "
          "hesaba emir gitmez)", flush=True)
    for mode, label in (("kagit", "Kâğıt İşlem"), ("demo", "Demo Mode"),
                        ("yari_otomatik", "Yarı Otomatik"), ("tam_otomatik", "Tam Otomatik")):
        before = [item for item, value in runtime.previous_modes.items() if value == mode]
        if before:
            print(f"                   Önceki oturumda {label}'da olanlar: "
                  f"{', '.join(before)}. Devam için arayüzden yeniden seçin.", flush=True)
    demo = runtime.demo
    if demo is None or demo.trader is None:
        demo_text = (demo.key_problem if demo is not None and demo.key_problem
                     else "kapalı")
    else:
        demo_text = ("anahtar bulundu; Binance Demo Mode'a (sahte para) bağlanıyor. "
                     "Durumu Demo işlem sekmesinde.")
    print(f"  Demo Mode      : {demo_text}", flush=True)
    live = runtime.live
    if live is None or live.trader is None:
        live_text = (live.key_problem if live is not None and live.key_problem
                     else "kapalı")
    else:
        live_text = ("anahtar bulundu; canlı hesaba (GERÇEK PARA) bağlanıyor, izinler "
                     "okunuyor. Coinler Sadece Öneri'de; canlı emir için Canlı işlem sekmesi.")
    print(f"  Canlı işlem    : {live_text}", flush=True)
    print(
        "  Piyasa verisi  : "
        + ("Binance genel veri akışı (hesap bilgisi yok, API anahtarı yok)" if online
           else "kapalı (--cevrimdisi); kâğıt işlem çalışmaz"),
        flush=True,
    )
    print(f"  Bildirimler    : {runtime.telegram_note}", flush=True)
    print(f"  Gözcü          : {runtime.watchdog_note}", flush=True)
    print(f"  Yedek          : günde bir, {backup.directory(state.veri_dizini).resolve()} "
          f"(son {backup.KEEP} yedek tutulur)", flush=True)
    if keychain.secret_directory() is not None:
        print(f"  Sır deposu     : {keychain.secret_directory()} (sunucu)", flush=True)
    print(f"  Komisyon       : {runtime.engine.costs_for(state.semboller[0]).kaynak_tr}",
          flush=True)
    # Aynı kimlik sayfanın en altında da yazar; ikisi farklıysa tarayıcı
    # eski bir sekmeyi gösteriyordur.
    print(f"  Arayüz sürümü  : {asset_version()}", flush=True)
    print(f"  Adres          : {address}", flush=True)
    print("\nDurdurmak için bu pencerede Control-C tuşlayın.", flush=True)
    print("Mac uyursa veri akışı durur; uyanınca kaçırılan mumlar işlenir.\n", flush=True)

    if not args.tarayici_acma:
        # Sunucu ayağa kalkmadan açılan sekme boş sayfa gösterir; bir saniye
        # gecikme kullanıcının "çalışmadı" sanmasını önlüyor.
        threading.Timer(1.5, lambda: webbrowser.open(address)).start()

    import uvicorn

    runtime.start()
    try:
        uvicorn.run(
            create_app(state),
            host=HOST,
            port=port,
            log_level="warning",
            access_log=False,
        )
    finally:
        print("\nKapatılıyor...", flush=True)
        runtime.stop()
        lock.release()
        print("Kapatıldı. Kâğıt emirler bir sonraki açılışta kaldığı yerden işlenir.",
              flush=True)
        if runtime.demo is not None and runtime.demo.trader is not None:
            print("Demo'daki açık pozisyonların stop ve hedefi borsada duruyor; bir sonraki "
                  "açılışta borsayla uzlaştırılır.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
