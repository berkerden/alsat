"""Binance Demo Mode anahtarını kurar ve Demo bağlantısını uçtan uca sınar.

Kullanım (Mac'te)::

    bash kurulum.sh demo-anahtar   # Demo anahtarı kur, izinleri ve komisyonu oku
    bash kurulum.sh demo-sina      # Demo'ya dolmayacak bir test emri gönder, iptal et

**Demo Mode sahte parayla çalışır**; canlı hesaba hiçbir istek gitmez
(adresler ``endpoints.py``'deki Demo adresleridir, ``DemoTrader`` başka
ortamı reddeder). Anahtar çifti bu bilgisayarda üretilir: özel yarısı Mac'in
Anahtar Zinciri'ne (``albsat-binance-demo`` kaydı) yazılır ve hiçbir yere
gönderilmez; Binance'e yalnızca genel yarısı yapıştırılır. Faz 4'ün salt
okuma anahtarı (``albsat-binance``) ayrı bir kayıttır ve değişmez.

Sınama emri borsadaki en iyi alışın yaklaşık %5 altına konan, dolmayacak
küçük bir OTOCO'dur (giriş + bekleyen hedef/stop). Gönderilmeden önce
sorulur; sonra akıştan geldiği, borsada göründüğü ve iptal edildiği
doğrulanır. Arayüz açıkken çalıştırılmamalıdır: arayüzün uzlaştırması
kaydında olmayan albsat alışlarını iptal eder.
"""

from __future__ import annotations

import argparse
import getpass
import queue
import re
import sys
import time
from collections.abc import Callable
from decimal import ROUND_UP, Decimal
from pathlib import Path
from typing import Any

from albsat.core import keychain
from albsat.core.fees import Side
from albsat.core.filters import SymbolRules
from albsat.core.tls import enable_system_trust
from albsat.data.commission import DEMO_FILENAME, CommissionStore
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
from albsat.exchange.trading import KEYCHAIN_SERVICE_DEMO, DemoTrader, OutcomeUnknown
from albsat.exchange.user_stream import OrderUpdate, UserStream
from albsat.execution import errors, ids
from albsat.execution.executor import DEMO_INFO_FILENAME, demo_account_problem
from albsat.execution.planner import Plan, plan_otoco
from albsat.execution.settings import ExecutionSettings

SYMBOLS = ("BTCUSDT", "SOLUSDT")
API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]{32,128}$")
API_PAGE = "https://demo.binance.com/en/my/settings/api-management"
#: Sınama emrinin girişi en iyi alışın bu oranı kadar aşağıda (dolmasın).
TEST_DISCOUNT = Decimal("0.95")
#: Sınama emrinin tutarı en küçük tutar sınırının bu katı (yuvarlamaya pay).
TEST_NOTIONAL_MARGIN = Decimal("1.5")
WAIT_SECONDS = 20.0

Say = Callable[[str], None]
Ask = Callable[[str], str]


def _say(text: str = "") -> None:
    print(text, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="albsat-demo",
                                     description="Binance Demo Mode anahtarı ve sınaması")
    parser.add_argument("--veri-dizini", default="./veri", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sina", action="store_true",
                       help="Demo'ya dolmayacak bir test emri gönderip iptal eder")
    group.add_argument("--sil", action="store_true",
                       help="Demo anahtarını sır deposundan siler")
    return parser


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _problem(error: BaseException) -> str:
    return errors.classify(error).mesaj_tr


# --- anahtar kurulumu ---------------------------------------------------------------


def setup(ask: Ask = input, secret: Ask = getpass.getpass, say: Say = _say) -> StoredKey | None:
    if load_key(KEYCHAIN_SERVICE_DEMO) is not None:
        answer = ask(
            "Bu bilgisayarda zaten kayıtlı bir Demo anahtarı var. Yenisiyle değiştirmek için e "
            "yazıp Enter'a basın (başka bir şey yazarsanız mevcut anahtar kullanılır): "
        ).strip().lower()
        if answer not in ("e", "evet"):
            return load_key(KEYCHAIN_SERVICE_DEMO)

    pending = keychain.read(KEYCHAIN_SERVICE_DEMO, ACCOUNT_PRIVATE)
    if pending is not None and keychain.read(KEYCHAIN_SERVICE_DEMO, ACCOUNT_API_KEY) is None:
        # Önceki denemede genel anahtar Binance'e verilmiş ama API Key girilmemiş
        # olabilir: aynı çift kullanılır.
        private, public_pem = pending, public_pem_of(pending)
        say("\nÖnceki denemeden kalan Demo anahtar çifti kullanılıyor.")
    else:
        private, public_pem = generate_keypair()
        try:
            keychain.write(KEYCHAIN_SERVICE_DEMO, ACCOUNT_PRIVATE, private)
        except keychain.KeychainError as error:
            say(f"! {error}")
            return None
        say("\nBu bilgisayarda Demo Mode için yeni bir Ed25519 anahtar çifti üretildi.")
    say("Özel yarısı bu bilgisayarda kalacak. Binance'e aşağıdaki GENEL yarıyı "
        "vereceksiniz.\n")
    say("----- Kopyalanacak metin (BEGIN ve END satırları dahil) -----")
    say(public_pem.strip())
    say("----- Kopyalanacak metnin sonu -----\n")
    say("Binance Demo Mode'da yapılacaklar (tarayıcıda):")
    say(f"  1) Şu sayfayı açın: {API_PAGE}")
    say("     (binance.com'a giriş yapıp Demo Trading'e geçince aynı sayfaya gelinir.)")
    say("  2) 'API Oluştur' → anahtar türü 'Kendi ürettiğim' (Self-generated).")
    say("  3) Etiket olarak 'albsat-demo' yazın.")
    say("  4) Genel anahtar kutusuna yukarıdaki metni yapıştırın.")
    say("  5) İzin ayarları Demo'da değiştirilemiyor; olduğu gibi bırakın.")
    say("  6) Binance size bir 'API Key' gösterecek (uzun bir harf-rakam dizisi).\n")
    try:
        api_key_text = secret(
            "O API Key'i buraya yapıştırıp Enter'a basın (ekranda görünmez): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        say("\nİptal edildi; API Key kaydedilmedi.")
        return None
    if not API_KEY_PATTERN.match(api_key_text):
        say("! Bu bir Binance API Key'ine benzemiyor (yalnızca harf ve rakamdan oluşur). "
            "Tekrar deneyin: bash kurulum.sh demo-anahtar")
        return None
    api_key = SecretText(api_key_text)
    del api_key_text
    try:
        keychain.write(KEYCHAIN_SERVICE_DEMO, ACCOUNT_API_KEY, api_key)
    except keychain.KeychainError as error:
        say(f"! {error}")
        return None
    say(f"✓ Demo anahtarı {keychain.where('e')} kaydedildi (depoda ve veri dizininde yok).")
    return StoredKey(api_key, private)


def _balances(payload: dict[str, Any], assets: set[str]) -> list[str]:
    lines = []
    for item in payload.get("balances", ()):
        asset = str(item.get("asset", ""))
        if asset in assets:
            free = _plain(Decimal(str(item.get("free", "0"))))
            locked = _plain(Decimal(str(item.get("locked", "0"))))
            lines.append(f"  {asset}: serbest {free}, emirde {locked}")
    return lines


def verify(trader: DemoTrader, root: Path, say: Say = _say) -> int:
    """Saat, izinler, bakiye ve komisyon: yalnızca okuma istekleri."""
    try:
        say("\nDemo saati kontrol ediliyor...")
        offset = trader.sync_time()
        say(f"✓ Saat farkı {offset} ms.")
        say("Demo hesabı okunuyor...")
        payload = trader.account()
    except Exception as error:  # noqa: BLE001 - kullanıcıya okunur hata
        say(f"! Binance Demo'ya sorulamadı: {_problem(error)}")
        return 1
    problem = demo_account_problem(payload)
    if problem is not None:
        say(f"✗ {problem}")
        return 1
    say("✓ Demo hesabı işleme açık (Demo Mode'da para çekme yoktur).")
    lines = _balances(payload, {"USDT", "BTC", "SOL", "BNB"})
    if lines:
        say("Demo bakiyesi (sahte para):")
        for line in lines:
            say(line)
    payloads = {}
    for symbol in SYMBOLS:
        say(f"{symbol} Demo komisyonu okunuyor...")
        try:
            payloads[symbol] = trader.commission(symbol)
        except Exception as error:  # noqa: BLE001
            say(f"! {symbol} komisyonu okunamadı: {_problem(error)}")
            return 1
    store = CommissionStore(root, DEMO_FILENAME)
    store.write(payloads)
    measured = store.read()
    if measured is not None:
        for symbol in SYMBOLS:
            say(f"  {symbol}: {measured.rates_text(symbol)}")
    say("\n✓ Demo anahtarı çalışıyor.")
    return 0


# --- uçtan uca sınama ---------------------------------------------------------------------


def probe_plan(rules: SymbolRules, bid: Decimal, fee_rate: Decimal, *,
               scheme: ids.IdScheme = ids.DEMO) -> Plan:
    """Dolmayacak küçük bir OTOCO: giriş en iyi alışın %5 altında, tutar en küçüğün 1,5 katı."""
    entry = rules.round_price(bid * TEST_DISCOUNT, Side.BUY)
    minimum = rules.notional.min_notional if rules.notional is not None else Decimal("5")
    quantity = (minimum * TEST_NOTIONAL_MARGIN / entry)
    if rules.lot is not None and rules.lot.step_size > 0:
        step = rules.lot.step_size
        quantity = (quantity / step).to_integral_value(rounding=ROUND_UP) * step
        quantity = max(quantity, rules.lot.min_qty)
    return plan_otoco(
        rules=rules, token=ids.new_token(), attempt=1, entry=entry,
        target=entry * Decimal("1.02"), stop=entry * Decimal("0.98"), quantity=quantity,
        fee_rate=fee_rate, carried_dust=Decimal("0"), settings=ExecutionSettings(),
        scheme=scheme,
    )


class _Events:
    """Hesap akışından gelen emir olaylarını bekler."""

    def __init__(self) -> None:
        self.queue: queue.Queue[Any] = queue.Queue()
        self.seen: list[OrderUpdate] = []

    def put(self, event: Any) -> None:
        self.queue.put(event)

    @staticmethod
    def _matches(event: OrderUpdate, client_id: str, statuses: set[str]) -> bool:
        # İptal olayında ``c`` iptal isteğinin kimliğidir, emrin kimliği ``C``'dedir.
        return client_id in (event.istemci_kimligi, event.asil_istemci_kimligi) \
            and event.durum in statuses

    def wait_for(self, client_id: str, statuses: set[str], seconds: float) -> OrderUpdate | None:
        deadline = time.monotonic() + seconds
        for event in self.seen:
            if self._matches(event, client_id, statuses):
                return event
        while time.monotonic() < deadline:
            try:
                event = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if isinstance(event, OrderUpdate):
                self.seen.append(event)
                if self._matches(event, client_id, statuses):
                    return event
        return None


def smoke(
    trader: DemoTrader,
    public: Any,
    stream_factory: Callable[..., Any],
    root: Path,
    *,
    symbol: str = "BTCUSDT",
    ask: Ask = input,
    say: Say = _say,
    wait_seconds: float = WAIT_SECONDS,
) -> int:
    """Demo'da uçtan uca sınama. Dönüş 0: her adım geçti."""
    total = 6
    say(f"\n1/{total}  Demo saati ve hesabı")
    try:
        offset = trader.sync_time()
        payload = trader.account()
    except Exception as error:  # noqa: BLE001
        say(f"✗ Binance Demo'ya sorulamadı: {_problem(error)}")
        return 1
    problem = demo_account_problem(payload)
    if problem is not None:
        say(f"✗ {problem}")
        return 1
    say(f"✓ Saat farkı {offset} ms; Demo hesabı işleme açık.")
    for line in _balances(payload, {"USDT", symbol.removesuffix("USDT")}):
        say(line)

    say(f"\n2/{total}  {symbol} için Demo borsa kuralları ve fiyatı")
    try:
        info = public.exchange_info(list(SYMBOLS))
        store = ExchangeInfoStore(root, DEMO_INFO_FILENAME)
        store.write(info)
        snapshot = store.read()
        rules = snapshot.rules_for(symbol) if snapshot is not None else None
        book = {str(item["symbol"]): item for item in public.book_tickers([symbol])}
    except Exception as error:  # noqa: BLE001
        say(f"✗ Demo borsa bilgisi alınamadı: {_problem(error)}")
        return 1
    if rules is None or symbol not in book:
        say(f"✗ {symbol} Demo borsasında bulunamadı.")
        return 1
    bid = Decimal(str(book[symbol]["bidPrice"]))
    ask_price = Decimal(str(book[symbol]["askPrice"]))
    say(f"✓ Kurallar alındı; Demo defterinde en iyi alış {_plain(bid)}, "
        f"en iyi satış {_plain(ask_price)}.")

    say(f"\n3/{total}  Demo hesap akışına bağlanılıyor (en fazla {wait_seconds:.0f} sn)")
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
        return _order_round(trader, rules, bid, events, symbol=symbol, ask=ask, say=say,
                            wait_seconds=wait_seconds, total=total)
    finally:
        stream.stop()


def _subscribed(stream: Any) -> bool:
    status = stream.status()
    return bool(getattr(status, "abone", False))


def _order_round(trader: DemoTrader, rules: SymbolRules, bid: Decimal, events: _Events, *,
                 symbol: str, ask: Ask, say: Say, wait_seconds: float, total: int) -> int:
    fee_rate = Decimal("0.001")
    plan = probe_plan(rules, bid, fee_rate)
    if not plan.gecerli:
        say("✗ Sınama emri borsa kurallarına uymuyor:")
        for item in plan.sorunlar:
            say(f"  - {item}")
        return 1
    params = plan.params
    amount = Decimal(params["workingPrice"]) * Decimal(params["workingQuantity"])
    say(f"\n4/{total}  Sınama emri (Demo, sahte para)")
    say(f"  Alış limiti {params['workingPrice']} × {params['workingQuantity']} "
        f"≈ {amount:.2f} USDT; en iyi alışın yaklaşık %5 altında, dolmaz.")
    say(f"  Dolarsa hedef {params['pendingAbovePrice']}, stop {params['pendingBelowStopPrice']}.")
    say("  Emir birkaç saniye içinde iptal edilecek.")
    answer = ask("Demo hesabına bu sınama emrini göndermek için e yazıp Enter'a basın: ")
    if answer.strip().lower() not in ("e", "evet"):
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
    # Bilgi amaçlı: girişi bekleyen hedef/stop kimliğiyle sorgulanabiliyor mu?
    # (Yürütücü sorgulanamazsa girişin dolumunda onları benimser.)
    for label, name in (("hedef", "pendingAboveClientOrderId"),
                        ("stop", "pendingBelowClientOrderId")):
        try:
            leg = trader.query_order(symbol, params[name])
            say(f"  Bekleyen {label}: {leg.get('status')}.")
        except Exception as error:  # noqa: BLE001
            say(f"  Bekleyen {label} sorgulanamadı ({_problem(error)}); bu beklenen bir durum "
                "olabilir, sorun değil.")

    say(f"\n6/{total}  Sınama emri iptal ediliyor")
    try:
        trader.cancel_order_list(symbol, list_id)
    except OutcomeUnknown:
        say("! İptal yanıtı gelmedi; borsa kaydına bakılıyor.")
    except Exception as error:  # noqa: BLE001
        say(f"✗ İptal gönderilemedi: {_problem(error)}")
        say(f"  Binance Demo'da açık emirlerden {working} kimlikli emri elle iptal edin.")
        return 1
    cancelled = events.wait_for(working, {"CANCELED", "EXPIRED"}, wait_seconds)
    try:
        final = trader.query_order(symbol, working)
    except Exception as error:  # noqa: BLE001
        say(f"✗ İptal borsada doğrulanamadı: {_problem(error)}")
        return 1
    if final.get("status") not in ("CANCELED", "EXPIRED"):
        say(f"✗ Emir hâlâ {final.get('status')} görünüyor. Binance Demo'da açık emirlerden "
            f"{working} kimlikli emri elle iptal edin.")
        return 1
    say("✓ İptal borsada görüldü" + (" ve akıştan geldi." if cancelled else
                                      "; akıştan iptal olayı gelmedi."))
    failed = failed or cancelled is None
    if failed:
        say("\n! Emir gönderme ve iptal çalışıyor ama hesap akışı olayları gecikti ya da "
            "gelmedi. Bu çıktıyı Claude ile paylaşın.")
        return 1
    say("\n✓ Demo Mode uçtan uca çalışıyor: emir gönderildi, akıştan izlendi, iptal edildi.")
    return 0


def remove(say: Say = _say) -> int:
    removed = [
        keychain.delete(KEYCHAIN_SERVICE_DEMO, ACCOUNT_API_KEY),
        keychain.delete(KEYCHAIN_SERVICE_DEMO, ACCOUNT_PRIVATE),
    ]
    say("✓ Demo anahtarı bu bilgisayardan silindi." if any(removed)
        else "Silinecek Demo anahtarı bulunamadı.")
    say(f"  Binance'teki Demo anahtarını da silmek için: {API_PAGE}")
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
    if args.sina:
        key = load_key(KEYCHAIN_SERVICE_DEMO)
        if key is None:
            _say("Kayıtlı Demo anahtarı yok. Önce: bash kurulum.sh demo-anahtar")
            return 1
        endpoints = endpoints_for(Environment.DEMO)
        stored: StoredKey = key
        trader = DemoTrader(stored, environment=Environment.DEMO, budget=budget)
        public = PublicHttp(rest_base=endpoints.rest, retries=1, backoff=1.0, budget=budget,
                            timeout=15.0)

        def stream_factory(**kwargs: Any) -> UserStream:
            return UserStream(endpoints.ws_api, key=stored, **kwargs)

        _say("Arayüz açıksa önce kapatın (arayüzün Terminal penceresinde Control-C).")
        return smoke(trader, public, stream_factory, root)
    key = setup()
    if key is None:
        return 1
    return verify(DemoTrader(key, environment=Environment.DEMO, budget=budget), root)


if __name__ == "__main__":
    sys.exit(main())
