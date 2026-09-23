"""Telegram bildirimlerini kuran komut.

Kullanım (Mac'te)::

    bash kurulum.sh telegram

Adımlar ekrana tek tek yazılır. Bot jetonu gizli girilir (ekranda görünmez)
ve yalnızca macOS Anahtar Zinciri'ne yazılır; ekrana, dosyaya, günlüğe
yazılmaz. Sohbet kimliği, kullanıcı bota ``/start`` yazdığında Telegram'ın
kendisinden okunur; elle girilmez.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

from albsat.core import keychain
from albsat.core.clock import iso, utc_now
from albsat.core.tls import enable_system_trust
from albsat.exchange.keys import SecretText
from albsat.notify.telegram import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    TelegramClient,
    TelegramConfig,
    TelegramError,
    load_token,
    looks_like_token,
)

WAIT_SECONDS = 180


def _say(text: str = "") -> None:
    print(text, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="albsat-telegram",
                                     description="Telegram bildirimlerini kurar")
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dene", action="store_true", help="Kurulu bota deneme mesajı gönder")
    group.add_argument("--sil", action="store_true", help="Telegram kurulumunu kaldır")
    return parser


def _wait_for_start(client: TelegramClient) -> tuple[int, str, int] | None:
    """Kullanıcının bota yazmasını bekler: (sohbet kimliği, ad, son güncelleme)."""
    deadline = time.monotonic() + WAIT_SECONDS
    offset: int | None = None
    dots = 0
    while time.monotonic() < deadline:
        try:
            updates = client.get_updates(offset, timeout=10)
        except TelegramError as error:
            _say(f"\n   ! {error}")
            time.sleep(3)
            continue
        for update in updates:
            offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            if chat.get("type") == "private" and chat.get("id") == sender.get("id"):
                name = " ".join(
                    part for part in (sender.get("first_name"), sender.get("last_name")) if part
                ) or str(sender.get("username") or "")
                return int(chat["id"]), name, int(update["update_id"])
        dots += 1
        remaining = int(deadline - time.monotonic())
        print(f"\r   Bekleniyor{'.' * (dots % 4):<3}  (kalan {max(remaining, 0)} sn) ",
              end="", flush=True)
    return None


def setup(root: Path) -> int:
    _say("Telegram bildirimleri kuruluyor.\n")
    _say("1) Telegram'da @BotFather adlı hesabı açın (mavi tikli olan).")
    _say("2) /newbot yazın. Bir ad, sonra 'bot' ile biten bir kullanıcı adı isteyecek.")
    _say("3) BotFather size bir jeton verecek; şuna benzer:")
    _say("      1234567890:AAH...uzun bir metin...")
    _say("   Bu jeton bir şifredir: kimseyle paylaşmayın, sohbete yapıştırmayın.\n")
    try:
        token_text = getpass.getpass(
            "Jetonu buraya yapıştırın ve Enter'a basın (güvenlik için ekranda görünmez): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        _say("\nİptal edildi.")
        return 1
    if not looks_like_token(token_text):
        _say("\n! Bu bir bot jetonuna benzemiyor (sayılar, iki nokta, sonra uzun bir metin "
             "olmalı). Tekrar deneyin: bash kurulum.sh telegram")
        return 1
    token = SecretText(token_text)
    del token_text
    client = TelegramClient(token)

    _say("\nBot kontrol ediliyor...")
    try:
        me = client.get_me()
    except TelegramError as error:
        _say(f"! Telegram botu doğrulayamadı: {error}")
        _say("  Jetonu BotFather'dan yeniden kopyalayıp tekrar deneyin.")
        return 1
    username = str(me.get("username") or "")
    _say(f"✓ Bot bulundu: @{username}\n")

    _say(f"4) Şimdi Telegram'da botunuzu açın: https://t.me/{username}")
    _say("   'Başlat' (Start) düğmesine basın ya da /start yazıp gönderin.")
    _say(f"   {WAIT_SECONDS // 60} dakika bekliyorum; mesajınız gelince devam edeceğim.\n")
    found = _wait_for_start(client)
    _say("")
    if found is None:
        _say("! Mesaj gelmedi. Bota /start yazdığınızdan emin olup tekrar deneyin: "
             "bash kurulum.sh telegram")
        return 1
    chat_id, name, last_update = found
    _say(f"✓ Mesaj geldi: '{name}' adlı hesaptan.")
    answer = input("   Bu siz misiniz? Evetse e yazıp Enter'a basın: ").strip().lower()
    if answer not in ("e", "evet", "y", "yes"):
        _say("İptal edildi; hiçbir şey kaydedilmedi.")
        return 1

    try:
        keychain.write(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, token)
    except keychain.KeychainError as error:
        _say(f"! {error}")
        return 1
    config = TelegramConfig(
        chat_id=chat_id, bot_kullanici_adi=username, kurulum_utc=iso(utc_now()),
        sohbet_adi=name,
    )
    path = config.save(root)
    # /start mesajını onayla: uygulama açılınca komut olarak işlenmesin.
    try:
        client.get_updates(last_update + 1, timeout=0)
        client.send_message(
            chat_id,
            "✅ Binance Al-Sat bildirimleri artık bu sohbete gelecek.\n"
            "Komutlar için /yardim yazın. Bu bot yalnızca size yanıt verir.",
        )
    except TelegramError as error:
        _say(f"! Deneme mesajı gönderilemedi: {error}")
        return 1
    _say("✓ Jeton macOS Anahtar Zinciri'ne kaydedildi (depoda ve dosyalarda yok).")
    _say(f"✓ Sohbet kimliği kaydedildi: {path}")
    _say("✓ Telegram'a deneme mesajı gönderildi.\n")
    _say("Arayüz açıksa kapatıp yeniden açın (Control-C, sonra bash kurulum.sh arayuz).")
    return 0


def test_message(root: Path) -> int:
    config = TelegramConfig.load(root)
    token = load_token()
    if config is None or token is None:
        _say("Telegram kurulu değil. Kurmak için: bash kurulum.sh telegram")
        return 1
    try:
        TelegramClient(token).send_message(config.chat_id, "🔔 Deneme mesajı.")
    except TelegramError as error:
        _say(f"! Gönderilemedi: {error}")
        return 1
    _say(f"✓ Deneme mesajı @{config.bot_kullanici_adi} üzerinden gönderildi.")
    return 0


def remove(root: Path) -> int:
    removed = keychain.delete(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    path = TelegramConfig.path(root)
    if path.exists():
        path.unlink()
    _say("✓ Telegram kurulumu kaldırıldı." if removed or not path.exists()
         else "Kaldırılacak kurulum bulunamadı.")
    _say("  Botu tamamen silmek isterseniz BotFather'a /deletebot yazın.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not keychain.available():
        _say("Telegram jetonu macOS Anahtar Zinciri'nde saklanır; bu komut yalnızca "
             "Mac'te çalışır.")
        return 1
    enable_system_trust()
    root = Path(args.veri_dizini)
    if args.dene:
        return test_message(root)
    if args.sil:
        return remove(root)
    return setup(root)


if __name__ == "__main__":
    sys.exit(main())
