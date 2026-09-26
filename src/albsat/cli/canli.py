"""Binance **canlı** işlem anahtarını kurar ve canlı bağlantıyı sınar (Faz 6).

Kullanım (Mac'te)::

    bash kurulum.sh canli-anahtar   # anahtarı kur; izinleri, hesabı, komisyonu oku
    bash kurulum.sh canli-sina      # dolmayacak bir sınama emri gönder, hemen iptal et

**Bu anahtar gerçek parayla işlem yapabilir.** Anahtar çifti bu bilgisayarda
üretilir: özel yarısı Mac'in Anahtar Zinciri'ne (``albsat-binance-canli``
kaydı) yazılır ve hiçbir yere gönderilmez; Binance'e yalnızca genel yarısı
yapıştırılır. Faz 4'ün salt okuma anahtarı (``albsat-binance``) ve Faz 5'in
Demo anahtarı (``albsat-binance-demo``) ayrı kayıtlardır ve değişmez.

``canli-anahtar`` yalnızca **okur**: saat, anahtarın izinleri
(``/sapi/v1/account/apiRestrictions``), hesap ve komisyon. Para çekme ya da
Spot dışı bir izin açıksa anahtarın kullanılmayacağını söyler.

``canli-sina`` canlı hesaba **gerçek** bir emir gönderir: en iyi alışın
yaklaşık %5 altında, en küçük tutarın 1,5 katı büyüklüğünde, dolmaması
beklenen bir OTOCO. Gönderilmeden önce coin adının yazılması istenir; sonra
akıştan geldiği, borsada göründüğü ve iptal edildiği doğrulanır. Emir
dolmadığı sürece para harcanmaz, yalnızca birkaç saniye emirde bekler.
Arayüz açıkken çalıştırılmamalıdır: arayüzün uzlaştırması kaydında olmayan
albsat alışlarını iptal eder.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from albsat.cli.demo import (
    API_KEY_PATTERN,
    WAIT_SECONDS,
    Ask,
    Say,
    _balances,
    _Events,
    _plain,
    _problem,
    _say,
    _subscribed,
    probe_plan,
)
from albsat.core import keychain
from albsat.core.filters import SymbolRules
from albsat.core.instance import held_by_other
from albsat.core.tls import enable_system_trust
from albsat.data.commission import CommissionStore
from albsat.data.exchangeinfo import ExchangeInfoStore
from albsat.exchange.endpoints import Environment, endpoints_for
from albsat.exchange.http import PublicHttp
from albsat.exchange.keys import SecretText
from albsat.exchange.ratelimit import RequestBudget
from albsat.exchange.signed import (
    ACCOUNT_API_KEY,
    ACCOUNT_PRIVATE,
    StoredKey,
    generate_keypair,
    load_key,
    public_pem_of,
)
from albsat.exchange.trading import (
    KEYCHAIN_SERVICE_LIVE,
    LiveTrader,
    OutcomeUnknown,
    PermissionState,
)
from albsat.exchange.user_stream import UserStream
from albsat.execution import ids
from albsat.execution.live import DEFAULT_CAP_USDT

SYMBOLS = ("BTCUSDT", "SOLUSDT")
API_PAGE = "https://www.binance.com/en/my/settings/api-management"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="albsat-canli",
                                     description="Binance canlı işlem anahtarı ve sınaması")
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sina", action="store_true",
                       help="Canlı hesaba dolmayacak bir sınama emri gönderip iptal eder")
    group.add_argument("--sil", action="store_true",
                       help="Canlı işlem anahtarını sır deposundan siler")
    return parser


# --- anahtar kurulumu ---------------------------------------------------------------


def setup(ask: Ask = input, secret: Ask = getpass.getpass, say: Say = _say) -> StoredKey | None:
    if load_key(KEYCHAIN_SERVICE_LIVE) is not None:
        answer = ask(
            "Bu bilgisayarda zaten kayıtlı bir canlı işlem anahtarı var. Yenisiyle değiştirmek "
            "için e yazıp Enter'a basın (başka bir şey yazarsanız mevcut anahtar kullanılır): "
        ).strip().lower()
        if answer not in ("e", "evet"):
            return load_key(KEYCHAIN_SERVICE_LIVE)

    pending = keychain.read(KEYCHAIN_SERVICE_LIVE, ACCOUNT_PRIVATE)
    if pending is not None and keychain.read(KEYCHAIN_SERVICE_LIVE, ACCOUNT_API_KEY) is None:
        private, public_pem = pending, public_pem_of(pending)
        say("\nÖnceki denemeden kalan canlı anahtar çifti kullanılıyor.")
    else:
        private, public_pem = generate_keypair()
        try:
            keychain.write(KEYCHAIN_SERVICE_LIVE, ACCOUNT_PRIVATE, private)
        except keychain.KeychainError as error:
            say(f"! {error}")
            return None
        say("\nBu bilgisayarda canlı işlem için yeni bir Ed25519 anahtar çifti üretildi.")
    say("Özel yarısı bu bilgisayarda kalacak. Binance'e aşağıdaki GENEL yarıyı "
        "vereceksiniz.\n")
    say("----- Kopyalanacak metin (BEGIN ve END satırları dahil) -----")
    say(public_pem.strip())
    say("----- Kopyalanacak metnin sonu -----\n")
    say("Binance'te yapılacaklar (tarayıcıda, GERÇEK hesabınızda):")
    say(f"  1) Şu sayfayı açın: {API_PAGE}")
    say("  2) 'API Oluştur' → anahtar türü 'Kendi ürettiğim' (Self-generated).")
    say("  3) Etiket olarak 'albsat-canli' yazın.")
    say("  4) Genel anahtar kutusuna yukarıdaki metni yapıştırın.")
    say("  5) İzinler: yalnızca 'Okuma' (Enable Reading) ve 'Spot ve Margin işlemleri'")
    say("     (Enable Spot & Margin Trading) AÇIK olsun. 'Para çekme' (Withdrawals), Margin,")
    say("     Vadeli (Futures), Opsiyon ve Transfer izinleri KAPALI kalsın. Uygulama bunları")
    say("     okuyup denetler; biri açıksa bu anahtarla emir göndermez.")
    if keychain.secret_directory() is not None:
        say("  6) IP kısıtlaması ZORUNLU: 'Yalnızca güvenilir IP'lerden erişim' (Restrict")
        say("     access to trusted IPs only) seçin ve sunucunun IP adresini yazın. IP'yi")
        say("     sunucuyu kiraladığınız firmanın panelinde görürsünüz. Kısıtsız anahtarla")
        say("     sunucu emir göndermez.")
    else:
        say("  6) IP kısıtlaması: Binance işlem izinli anahtarda güvenilir IP kısıtı öneriyor.")
        say("     Ev IP'niz değişirse kısıtlı anahtar çalışmaz (borsadaki stop ve hedef yerinde")
        say("     kalır). Seçiminizi README'deki 'Canlı işlem anahtarı' bölümüne göre yapın.")
    say("  7) Binance size bir 'API Key' gösterecek (uzun bir harf-rakam dizisi).\n")
    try:
        api_key_text = secret(
            "O API Key'i buraya yapıştırıp Enter'a basın (ekranda görünmez): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        say("\nİptal edildi; API Key kaydedilmedi.")
        return None
    if not API_KEY_PATTERN.match(api_key_text):
        say("! Bu bir Binance API Key'ine benzemiyor (yalnızca harf ve rakamdan oluşur). "
            "Tekrar deneyin: bash kurulum.sh canli-anahtar")
        return None
    api_key = SecretText(api_key_text)
    del api_key_text
    try:
        keychain.write(KEYCHAIN_SERVICE_LIVE, ACCOUNT_API_KEY, api_key)
    except keychain.KeychainError as error:
        say(f"! {error}")
        return None
    say(f"✓ Canlı işlem anahtarı {keychain.where('e')} kaydedildi (depoda ve veri "
        "dizininde yok).")
    return StoredKey(api_key, private)


def _permission_report(state: PermissionState, say: Say) -> None:
    def mark(value: bool | None) -> str:
        return "?" if value is None else ("AÇIK" if value else "kapalı")

    say(f"  Para çekme       : {mark(state.cekim_izni)}")
    say(f"  Spot işlem       : {mark(state.islem_izni)}")
    say(f"  IP kısıtlaması   : {mark(state.ip_kisitli)}")


def check_permissions(trader: LiveTrader, say: Say = _say) -> bool:
    """Anahtarın izinleri: para çekme ve Spot dışı izinler kapalı, Spot işlem açık."""
    say("Anahtarın izinleri okunuyor (apiRestrictions)...")
    try:
        state = trader.verify_permissions()
    except Exception as error:  # noqa: BLE001 - kullanıcıya okunur hata
        say(f"! İzinler okunamadı: {_problem(error)}")
        return False
    _permission_report(state, say)
    for warning in state.uyarilar:
        say(f"  Not: {warning}")
    if not state.tamam:
        for problem in state.engeller:
            say(f"✗ {problem}")
        say(f"  Binance'te düzeltin ({API_PAGE}) ve yeniden deneyin: "
            "bash kurulum.sh canli-anahtar")
        return False
    say("✓ İzinler uygun: para çekme ve diğer riskli izinler kapalı, Spot işlem açık.")
    return True


def account_problem(payload: dict[str, Any]) -> str | None:
    if not payload.get("canTrade"):
        return ("Canlı hesap işlem yapamıyor görünüyor (Binance 'canTrade' kapalı diyor). "
                "Hesabınızın ve anahtarın durumunu Binance'te kontrol edin.")
    kind = payload.get("accountType")
    if kind and kind != "SPOT":
        return f"Hesap türü {kind}; bu uygulama yalnızca Spot hesapla çalışır."
    return None


def verify(trader: LiveTrader, root: Path, say: Say = _say) -> int:
    """Saat, izinler, hesap, komisyon: yalnızca okuma istekleri. Emir göndermez."""
    try:
        say("\nBinance saati kontrol ediliyor...")
        offset = trader.sync_time()
        say(f"✓ Saat farkı {offset} ms.")
    except Exception as error:  # noqa: BLE001
        say(f"! Binance'e sorulamadı: {_problem(error)}")
        return 1
    if not check_permissions(trader, say):
        return 1
    say("Canlı hesap okunuyor...")
    try:
        payload = trader.account()
    except Exception as error:  # noqa: BLE001
        say(f"! Hesap okunamadı: {_problem(error)}")
        return 1
    problem = account_problem(payload)
    if problem is not None:
        say(f"✗ {problem}")
        return 1
    say("✓ Canlı hesap Spot işleme açık.")
    lines = _balances(payload, {"USDT", "BTC", "SOL", "BNB"})
    if lines:
        say("Canlı bakiye (gerçek para):")
        for line in lines:
            say(line)
    payloads = {}
    for symbol in SYMBOLS:
        say(f"{symbol} komisyonu okunuyor...")
        try:
            payloads[symbol] = trader.commission(symbol)
        except Exception as error:  # noqa: BLE001
            say(f"! {symbol} komisyonu okunamadı: {_problem(error)}")
            return 1
    store = CommissionStore(root)
    store.write(payloads)
    measured = store.read()
    if measured is not None:
        for symbol in SYMBOLS:
            say(f"  {symbol}: {measured.rates_text(symbol)}")
    say("\n✓ Canlı işlem anahtarı çalışıyor. Bu adım hiçbir emir göndermedi.")
    return 0


# --- uçtan uca sınama ---------------------------------------------------------------------


def smoke(
    trader: LiveTrader,
    public: Any,
    stream_factory: Callable[..., Any],
    root: Path,
    *,
    symbol: str = "BTCUSDT",
    ask: Ask = input,
    say: Say = _say,
    wait_seconds: float = WAIT_SECONDS,
) -> int:
    """Canlı hesapta uçtan uca sınama. Dönüş 0: her adım geçti."""
    total = 6
    say(f"\n1/{total}  Saat, anahtar izinleri ve canlı hesap")
    try:
        offset = trader.sync_time()
    except Exception as error:  # noqa: BLE001
        say(f"✗ Binance'e sorulamadı: {_problem(error)}")
        return 1
    say(f"✓ Saat farkı {offset} ms.")
    if not check_permissions(trader, say):
        return 1
    try:
        payload = trader.account()
    except Exception as error:  # noqa: BLE001
        say(f"✗ Hesap okunamadı: {_problem(error)}")
        return 1
    problem = account_problem(payload)
    if problem is not None:
        say(f"✗ {problem}")
        return 1
    free_usdt = Decimal("0")
    for item in payload.get("balances", ()):
        if item.get("asset") == "USDT":
            free_usdt = Decimal(str(item.get("free", "0")))
    for line in _balances(payload, {"USDT", symbol.removesuffix("USDT")}):
        say(line)

    say(f"\n2/{total}  {symbol} için borsa kuralları ve fiyatı")
    try:
        info = public.exchange_info(list(SYMBOLS))
        store = ExchangeInfoStore(root)
        store.write(info)
        snapshot = store.read()
        rules = snapshot.rules_for(symbol) if snapshot is not None else None
        book = {str(item["symbol"]): item for item in public.book_tickers([symbol])}
    except Exception as error:  # noqa: BLE001
        say(f"✗ Borsa bilgisi alınamadı: {_problem(error)}")
        return 1
    if rules is None or symbol not in book:
        say(f"✗ {symbol} borsada bulunamadı.")
        return 1
    bid = Decimal(str(book[symbol]["bidPrice"]))
    ask_price = Decimal(str(book[symbol]["askPrice"]))
    say(f"✓ Kurallar alındı; en iyi alış {_plain(bid)}, en iyi satış {_plain(ask_price)}.")

    say(f"\n3/{total}  Canlı hesap akışına bağlanılıyor (en fazla {wait_seconds:.0f} sn)")
    events = _Events()
    stream = stream_factory(on_event=events.put, server_time_ms=trader.now_ms)
    stream.start()
    try:
        started = time.monotonic()
        shown = 0
        while not _subscribed(stream) and time.monotonic() - started < wait_seconds:
            time.sleep(0.5)
            waited = int(time.monotonic() - started)
            if waited >= shown + 3:
                shown = waited
                say(f"  bekleniyor ({waited} sn)...")
        if not _subscribed(stream):
            reason = getattr(stream.status(), "son_hata", None) or "yanıt yok"
            say(f"✗ Hesap akışına abone olunamadı: {reason}")
            return 1
        say("✓ Hesap akışına abone olundu.")
        return _order_round(trader, rules, bid, free_usdt, events, symbol=symbol, ask=ask,
                            say=say, wait_seconds=wait_seconds, total=total)
    finally:
        stream.stop()


def _order_round(trader: LiveTrader, rules: SymbolRules, bid: Decimal, free_usdt: Decimal,
                 events: _Events, *, symbol: str, ask: Ask, say: Say, wait_seconds: float,
                 total: int) -> int:
    plan = probe_plan(rules, bid, Decimal("0.001"), scheme=ids.LIVE)
    if not plan.gecerli:
        say("✗ Sınama emri borsa kurallarına uymuyor:")
        for item in plan.sorunlar:
            say(f"  - {item}")
        return 1
    params = plan.params
    amount = Decimal(params["workingPrice"]) * Decimal(params["workingQuantity"])
    if amount > DEFAULT_CAP_USDT:
        say(f"✗ Sınama emri ({amount:.2f} USDT) canlı emir tavanını ({DEFAULT_CAP_USDT} USDT) "
            "aşıyor; gönderilmedi.")
        return 1
    if free_usdt < amount:
        say(f"✗ Canlı hesapta serbest USDT {_plain(free_usdt)}; sınama emri için en az "
            f"{amount:.2f} USDT gerekir. Emir gönderilmedi.")
        return 1
    say(f"\n4/{total}  Sınama emri (CANLI HESAP, GERÇEK PARA)")
    say(f"  Alış limiti {params['workingPrice']} × {params['workingQuantity']} "
        f"≈ {amount:.2f} USDT; en iyi alışın yaklaşık %5 altında, dolması beklenmez.")
    say(f"  Dolarsa hedef {params['pendingAbovePrice']}, stop {params['pendingBelowStopPrice']}.")
    say("  Emir birkaç saniye içinde iptal edilecek. Dolmadığı sürece para harcanmaz.")
    answer = ask(f"Canlı hesaba bu sınama emrini göndermek için {symbol} yazıp Enter'a basın: ")
    if answer.strip().upper() != symbol:
        say("Sınama emri gönderilmedi.")
        return 1
    list_id = params["listClientOrderId"]
    working = params["workingClientOrderId"]
    try:
        trader.place_otoco(params)
        say("✓ Borsa emri kabul etti.")
    except OutcomeUnknown:
        say("! Borsadan yanıt gelmedi; emir kimliğiyle sorgulanacak (yeniden gönderilmez).")
    except Exception as error:  # noqa: BLE001
        say(f"✗ Emir gönderilemedi: {_problem(error)}")
        return 1

    say(f"\n5/{total}  Akış ve borsa kaydı doğrulanıyor")
    seen = events.wait_for(working, {"NEW"}, wait_seconds)
    say("✓ Hesap akışından emir olayı geldi." if seen else
        "! Akıştan emir olayı gelmedi; borsa kaydına bakılıyor.")
    failed = seen is None
    try:
        order = trader.query_order(symbol, working)
        say(f"✓ Borsada giriş emri: {order.get('status')}.")
    except Exception as error:  # noqa: BLE001
        say(f"✗ Giriş emri borsada sorgulanamadı: {_problem(error)}")
        failed = True

    say(f"\n6/{total}  Sınama emri iptal ediliyor")
    try:
        trader.cancel_order_list(symbol, list_id)
    except OutcomeUnknown:
        say("! İptal yanıtı gelmedi; borsa kaydına bakılıyor.")
    except Exception as error:  # noqa: BLE001
        say(f"✗ İptal gönderilemedi: {_problem(error)}")
        say(f"  Binance'te 'Açık Emirler'den {working} kimlikli emri elle iptal edin.")
        return 1
    cancelled = events.wait_for(working, {"CANCELED", "EXPIRED"}, wait_seconds)
    try:
        final = trader.query_order(symbol, working)
    except Exception as error:  # noqa: BLE001
        say(f"✗ İptal borsada doğrulanamadı: {_problem(error)}")
        say(f"  Binance'te 'Açık Emirler'e bakın; {working} kimlikli emir varsa elle iptal edin.")
        return 1
    status = final.get("status")
    if status == "FILLED" or Decimal(str(final.get("executedQty", "0"))) > 0:
        say("! Sınama emri beklenmedik biçimde doldu. Borsa hedef ve stopu kendisi koydu; "
            "arayüzü açıp Canlı işlem sekmesinden 'Uzlaştır' deyin ve bu çıktıyı Claude ile "
            "paylaşın.")
        return 1
    if status not in ("CANCELED", "EXPIRED"):
        say(f"✗ Emir hâlâ {status} görünüyor. Binance'te 'Açık Emirler'den {working} "
            "kimlikli emri elle iptal edin.")
        return 1
    say("✓ İptal borsada görüldü" + (" ve akıştan geldi." if cancelled else
                                      "; akıştan iptal olayı gelmedi."))
    failed = failed or cancelled is None
    if failed:
        say("\n! Emir gönderme ve iptal çalışıyor ama hesap akışı olayları gecikti ya da "
            "gelmedi. Bu çıktıyı Claude ile paylaşın.")
        return 1
    say("\n✓ Canlı bağlantı uçtan uca çalışıyor: emir gönderildi, akıştan izlendi, iptal "
        "edildi. Hiçbir alım yapılmadı.")
    return 0


def remove(say: Say = _say) -> int:
    removed = [
        keychain.delete(KEYCHAIN_SERVICE_LIVE, ACCOUNT_API_KEY),
        keychain.delete(KEYCHAIN_SERVICE_LIVE, ACCOUNT_PRIVATE),
    ]
    say("✓ Canlı işlem anahtarı bu bilgisayardan silindi." if any(removed)
        else "Silinecek canlı işlem anahtarı bulunamadı.")
    say(f"  Binance'teki anahtarı da silmek için: {API_PAGE}")
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
    budget = RequestBudget(local_cap=100)
    endpoints = endpoints_for(Environment.LIVE)
    if args.sina:
        key = load_key(KEYCHAIN_SERVICE_LIVE)
        if key is None:
            _say("Kayıtlı canlı işlem anahtarı yok. Önce: bash kurulum.sh canli-anahtar")
            return 1
        stored: StoredKey = key
        trader = LiveTrader(stored, environment=Environment.LIVE, budget=budget,
                            entry_cap_usdt=lambda: DEFAULT_CAP_USDT)
        public = PublicHttp(rest_base=endpoints.rest, retries=1, backoff=1.0, budget=budget,
                            timeout=15.0)

        def stream_factory(**kwargs: Any) -> UserStream:
            return UserStream(endpoints.ws_api, key=stored, **kwargs)

        holder = held_by_other(root)
        if holder is not None:
            # Faz 6 tuzak 5: açık uygulama sınama emrini "yetim" sayıp iptal eder ve
            # istek bütçesini göremez. Faz 7'den beri yalnızca söylenmiyor, engelleniyor.
            _say(f"Uygulama açık ({holder}). Sınama emri gönderilmedi.")
            _say("Önce uygulamayı kapatın (arayüzün Terminal penceresinde Control-C), "
                 "sonra bu komutu yeniden çalıştırın.")
            return 1
        return smoke(trader, public, stream_factory, root)
    key = setup()
    if key is None:
        return 1
    return verify(LiveTrader(key, environment=Environment.LIVE, budget=budget), root)


if __name__ == "__main__":
    sys.exit(main())
