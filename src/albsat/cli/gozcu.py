"""Dış gözcüyü (uygulama kapanınca alarm) kuran ve sınayan komut.

Kullanım (Mac'te)::

    bash kurulum.sh gozcu            # kur
    bash kurulum.sh gozcu --sina     # alarm sınaması
    bash kurulum.sh gozcu --sil      # kaldır

Gözcü hizmeti Healthchecks.io'dur (``notify.gozcu``). Ping adresi sır
sayılır: Anahtar Zinciri'ne (sunucuda sır dizinine) yazılır, ekrana yalnızca
sunucu adı yazılır.
"""

from __future__ import annotations

import argparse
import sys
import time

from albsat.core import keychain
from albsat.core.tls import enable_system_trust
from albsat.exchange.keys import SecretText
from albsat.notify.gozcu import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    PingClient,
    WatchdogError,
    load_url,
    valid_url,
)

#: Alarm sınamasında "sorun var" ile "düzeldi" arasındaki bekleme.
RECOVER_AFTER_SECONDS = 30


def _say(text: str = "") -> None:
    print(text, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="albsat-gozcu",
                                     description="Uygulama kapanınca alarm veren gözcüyü kurar")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sina", action="store_true",
                       help="Alarm sınaması: gözcüye 'sorun var', sonra 'düzeldi' gönderir")
    group.add_argument("--sil", action="store_true", help="Gözcü adresini sil")
    return parser


def setup() -> int:
    _say("Healthchecks.io'da oluşturduğunuz kontrolün ping adresini yapıştırın.")
    _say("Adres https://hc-ping.com/ ile başlar. Yapıştırıp Enter'a basın.")
    try:
        text = input("Ping adresi: ").strip()
    except (EOFError, KeyboardInterrupt):
        _say("\nVazgeçildi; hiçbir şey kaydedilmedi.")
        return 1
    if not valid_url(text):
        _say("! Bu bir ping adresine benzemiyor (https:// ile başlayan tam adres olmalı).")
        _say("  Hiçbir şey kaydedilmedi. Adresi yeniden kopyalayıp komutu tekrar çalıştırın.")
        return 1
    url = SecretText(text)
    client = PingClient(url)
    _say(f"Deneme pingi gönderiliyor ({client.host})...")
    try:
        client.ok("albsat-gozcu kurulum denemesi")
    except WatchdogError as error:
        _say(f"! {error}")
        _say("  Hiçbir şey kaydedilmedi. Adresi kontrol edip tekrar deneyin.")
        return 1
    try:
        keychain.write(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, url)
    except keychain.KeychainError as error:
        _say(f"! {error}")
        return 1
    _say(f"✓ Gözcü kabul etti ve adres {keychain.store_name()} kaydedildi.")
    _say("  Healthchecks.io'daki kontrol şimdi yeşil ('up') görünmeli.")
    _say("  Uygulama açıksa kapatıp yeniden açın; gözcüye 2 dakikada bir ping gider.")
    return 0


def alarm_test() -> int:
    url = load_url()
    if url is None:
        _say("Gözcü kurulu değil. Kurmak için önce gözcüyü kurun (bash kurulum.sh gozcu).")
        return 1
    client = PingClient(url)
    _say(f"Gözcüye ({client.host}) 'sorun var' gönderiliyor...")
    try:
        client.fail("Alarm sınaması (bash kurulum.sh gozcu --sina). Gerçek bir sorun yok.")
    except WatchdogError as error:
        _say(f"! {error}")
        return 1
    _say("✓ Gönderildi. Birkaç saniye içinde telefonunuza 'down' alarmı gelmeli.")
    _say(f"  {RECOVER_AFTER_SECONDS} saniye sonra 'düzeldi' gönderilecek; o zaman 'up' mesajı "
         "gelmeli.")
    for remaining in range(RECOVER_AFTER_SECONDS, 0, -5):
        _say(f"  ... {remaining} sn")
        time.sleep(5)
    try:
        client.ok("Alarm sınaması bitti")
    except WatchdogError as error:
        _say(f"! {error}")
        return 1
    _say("✓ 'Düzeldi' gönderildi. İki mesaj da geldiyse alarm yolu çalışıyor.")
    return 0


def remove() -> int:
    removed = keychain.delete(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    _say("✓ Gözcü adresi silindi. Healthchecks.io'daki kontrolü de silebilir ya da "
         "duraklatabilirsiniz; yoksa alarm verir." if removed
         else "Silinecek gözcü adresi bulunamadı.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not keychain.available():
        _say(keychain.unavailable_reason())
        return 1
    enable_system_trust()
    if args.sina:
        return alarm_test()
    if args.sil:
        return remove()
    return setup()


if __name__ == "__main__":
    sys.exit(main())
