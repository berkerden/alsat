"""Binance API anahtarını kurar ve hesaba özel komisyonu ölçer.

Kullanım (Mac'te)::

    bash kurulum.sh anahtar     # anahtar kur + komisyonu ölç
    bash kurulum.sh komisyon    # yalnızca komisyonu yeniden ölç

**Anahtar çifti bu bilgisayarda üretilir.** Özel yarısı Mac'in Anahtar
Zinciri'ne yazılır ve hiçbir yere gönderilmez; Binance'e yalnızca **genel**
yarısı yapıştırılır (Binance'in "kendi ürettiğim anahtar" seçeneği).
Binance'in verdiği API anahtarı kimliği de Anahtar Zinciri'ne yazılır.

Bu komut yalnızca iki imzalı **okuma** isteği yapar: anahtarın izinleri
ve komisyon oranı. Para çekme izni açıksa komisyon okunmaz, uygulama
anahtarı kullanmayı reddeder.
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from decimal import Decimal
from pathlib import Path

from albsat.core import keychain
from albsat.core.tls import enable_system_trust
from albsat.data.commission import ASSUMED_RATE, CommissionStore
from albsat.exchange.http import HttpError
from albsat.exchange.keys import SecretText
from albsat.exchange.ratelimit import BudgetExceeded, RequestBudget
from albsat.exchange.signed import (
    ACCOUNT_API_KEY,
    ACCOUNT_PRIVATE,
    KEYCHAIN_SERVICE,
    SignedReader,
    StoredKey,
    generate_keypair,
    load_key,
    public_pem_of,
    restriction_problems,
)

SYMBOLS = ("BTCUSDT", "SOLUSDT")
ASSUMED_RATE_DEC = Decimal(ASSUMED_RATE)
API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]{32,128}$")


def _say(text: str = "") -> None:
    print(text, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="albsat-anahtar",
                                     description="API anahtarı kurar, komisyonu ölçer")
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--olc", action="store_true", help="Yalnızca komisyonu ölç")
    group.add_argument("--sil", action="store_true",
                       help="Anahtarı sır deposundan (Mac'te Anahtar Zinciri) sil")
    return parser


def setup() -> StoredKey | None:
    if load_key() is not None:
        answer = input(
            "Bu Mac'te zaten kayıtlı bir anahtar var. Yenisiyle değiştirmek için e yazıp "
            "Enter'a basın (başka bir şey yazarsanız mevcut anahtar kullanılır): "
        ).strip().lower()
        if answer not in ("e", "evet"):
            return load_key()

    pending = keychain.read(KEYCHAIN_SERVICE, ACCOUNT_PRIVATE)
    if pending is not None and keychain.read(KEYCHAIN_SERVICE, ACCOUNT_API_KEY) is None:
        # Önceki denemede genel anahtar Binance'e verilmiş ama API Key girilmemiş
        # olabilir: aynı çift kullanılır, Binance'teki anahtar boşa gitmez.
        private, public_pem = pending, public_pem_of(pending)
        _say("\nÖnceki denemeden kalan anahtar çifti kullanılıyor.")
    else:
        private, public_pem = generate_keypair()
        try:
            keychain.write(KEYCHAIN_SERVICE, ACCOUNT_PRIVATE, private)
        except keychain.KeychainError as error:
            _say(f"! {error}")
            return None
        _say("\nBu bilgisayarda yeni bir Ed25519 anahtar çifti üretildi.")
    _say("Özel yarısı Mac'inizde kalacak. Binance'e aşağıdaki GENEL yarıyı vereceksiniz.\n")
    _say("----- Kopyalanacak metin (BEGIN ve END satırları dahil) -----")
    _say(public_pem.strip())
    _say("----- Kopyalanacak metnin sonu -----\n")
    _say("Binance'te yapılacaklar (tarayıcıda, binance.com):")
    _say("  1) Profil simgesi → Hesap → API Yönetimi → 'API Oluştur'.")
    _say("  2) Anahtar türü olarak 'Kendi ürettiğim' (Self-generated) seçeneğini seçin.")
    _say("  3) Etiket olarak 'albsat-okuma' yazın.")
    _say("  4) Genel anahtar kutusuna yukarıdaki metni yapıştırın, doğrulamaları yapın.")
    _say("  5) Anahtar oluşunca 'Düzenle'ye basın: yalnızca 'Okumayı etkinleştir' açık")
    _say("     kalsın. Para çekme, Spot işlem, Margin, Vadeli işlem KAPALI olsun.")
    _say("  6) Binance size bir 'API Key' gösterecek (uzun bir harf-rakam dizisi).\n")
    try:
        api_key_text = getpass.getpass(
            "O API Key'i buraya yapıştırıp Enter'a basın (ekranda görünmez): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        _say("\nİptal edildi; hiçbir şey kaydedilmedi.")
        return None
    if not API_KEY_PATTERN.match(api_key_text):
        _say("! Bu bir Binance API Key'ine benzemiyor (yalnızca harf ve rakamdan oluşur). "
             "Tekrar deneyin: bash kurulum.sh anahtar")
        return None
    api_key = SecretText(api_key_text)
    del api_key_text
    try:
        keychain.write(KEYCHAIN_SERVICE, ACCOUNT_API_KEY, api_key)
    except keychain.KeychainError as error:
        _say(f"! {error}")
        return None
    _say(f"✓ Anahtar {keychain.where('e')} kaydedildi (depoda ve veri dizininde yok).")
    return StoredKey(api_key, private)


def measure(key: StoredKey, root: Path) -> int:
    reader = SignedReader(key, budget=RequestBudget(local_cap=100))
    try:
        _say("\nBinance saati kontrol ediliyor...")
        offset = reader.sync_time()
        _say(f"✓ Saat farkı {offset} ms.")
        _say("Anahtarın izinleri okunuyor...")
        restrictions = reader.api_restrictions()
    except (HttpError, BudgetExceeded, OSError) as error:
        _say(f"! Binance'e sorulamadı: {error}")
        return 1
    blocking, warnings = restriction_problems(restrictions)
    for item in warnings:
        _say(f"  ! {item}")
    if blocking:
        _say("\nBu anahtar KULLANILMAYACAK:")
        for item in blocking:
            _say(f"  ✗ {item}")
        _say("Binance'te anahtarın izinlerini düzeltip şunu çalıştırın: bash kurulum.sh komisyon")
        return 1
    _say("✓ Para çekme izni kapalı; anahtar yalnızca okuma için kullanılacak.")

    payloads = {}
    for symbol in SYMBOLS:
        _say(f"{symbol} komisyonu okunuyor...")
        try:
            payloads[symbol] = reader.commission(symbol)
        except (HttpError, BudgetExceeded, OSError) as error:
            _say(f"! {symbol} komisyonu okunamadı: {error}")
            return 1
    path = CommissionStore(root).write(payloads)
    measured = CommissionStore(root).read()
    assert measured is not None
    _say("")
    same = True
    for symbol in SYMBOLS:
        _say(f"  {symbol}: {measured.rates_text(symbol)}")
        table = measured.tablolar[symbol]
        if (table.standard.maker, table.standard.taker) != (
            ASSUMED_RATE_DEC, ASSUMED_RATE_DEC
        ) or table.tax.maker or table.special.maker:
            same = False
        if table.discount.enabled_for_account:
            _say("    BNB ile ödeme indirimi hesabınızda açık görünüyor; kâğıt işlem "
                 "temkinli olmak için indirimsiz oranı kullanır.")
    _say(f"\n✓ Kaydedildi: {path}")
    if same:
        _say("Oranlarınız Faz 1-2'de varsayılan %0.1 ile aynı: önceki sonuçlar geçerli.")
    else:
        _say("Oranlarınız %0.1 varsayımından farklı. Kâğıt işlem bundan sonra bu oranları "
             "kullanacak. Örüntü taramasını yeni oranlarla tekrarlamak için:\n"
             "    bash kurulum.sh tarama")
    _say("Arayüz açıksa kapatıp yeniden açın (Control-C, sonra bash kurulum.sh arayuz).")
    return 0


def remove() -> int:
    removed = [
        keychain.delete(KEYCHAIN_SERVICE, ACCOUNT_API_KEY),
        keychain.delete(KEYCHAIN_SERVICE, ACCOUNT_PRIVATE),
    ]
    _say("✓ Anahtar bu Mac'ten silindi." if any(removed) else "Silinecek anahtar bulunamadı.")
    _say("  Binance'teki API anahtarını da silmek için: Binance → API Yönetimi → Sil.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not keychain.available():
        _say(keychain.unavailable_reason())
        return 1
    if args.sil:
        return remove()
    enable_system_trust()
    root = Path(args.veri_dizini)
    if args.olc:
        key = load_key()
        if key is None:
            _say("Kayıtlı anahtar yok. Önce: bash kurulum.sh anahtar")
            return 1
    else:
        key = setup()
        if key is None:
            return 1
    return measure(key, root)


if __name__ == "__main__":
    sys.exit(main())
