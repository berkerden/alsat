"""Demo ve canlı yürütücülerin kurulumu: anahtar, istemci, akışlar.

Anahtarlar yalnızca burada, sır deposundan okunur (Mac'te Anahtar Zinciri,
sunucuda ``core.keychain``'in korumalı dizini; Demo: ``albsat-binance-demo``,
canlı işlem: ``albsat-binance-canli``). Arayüz
katmanı anahtara ve imzalı istemciye hiç dokunmaz; yalnızca kurulmuş
yürütücüyle konuşur.

Anahtar yoksa (ya da sır deposu yoksa) yürütücü yine kurulur ama
``trader`` boştur: sekme açılır, mod seçilemez ve nedeni yazılır.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from albsat.core import keychain
from albsat.data.live import LiveMarket
from albsat.data.store import KlineStore
from albsat.exchange.endpoints import Environment, endpoints_for
from albsat.exchange.http import PublicHttp
from albsat.exchange.market_stream import MarketStream, stream_url
from albsat.exchange.ratelimit import RequestBudget
from albsat.exchange.signed import load_key
from albsat.exchange.trading import (
    KEYCHAIN_SERVICE_DEMO,
    KEYCHAIN_SERVICE_LIVE,
    DemoTrader,
    LiveTrader,
)
from albsat.exchange.user_stream import UserStream
from albsat.execution.executor import DemoExecutor
from albsat.execution.live import LiveExecutor
from albsat.execution.venue import LIVE
from albsat.notify.base import KIND_LIMIT, Notifier
from albsat.paper.engine import PaperEngine
from albsat.strategy.rules import RuleSet

NO_KEY = ("Demo Mode anahtarı kurulu değil. Kurmak için Terminal'de: "
          "bash kurulum.sh demo-anahtar")
NOT_MAC = "Demo anahtarı okunamıyor: bu bilgisayarda sır deposu yok."
OFFLINE = "Bu çalıştırmada ağ kapalı; Demo Mode kullanılamaz."
LIVE_NOT_MAC = "Canlı işlem anahtarı okunamıyor: bu bilgisayarda sır deposu yok."
LIVE_OFFLINE = "Bu çalıştırmada ağ kapalı; canlı işlem kullanılamaz."


def build_executor(
    root: Path | str,
    *,
    symbols: Sequence[str],
    engine: PaperEngine,
    live_market: LiveMarket,
    notifier: Notifier,
    online: bool,
    key_loader: Callable[[], Any] | None = None,
) -> DemoExecutor:
    root = Path(root)
    symbols = tuple(symbols)
    demo_market = LiveMarket(KlineStore(root))

    def offline(problem: str) -> DemoExecutor:
        return DemoExecutor(root, symbols=symbols, engine=engine, live_market=live_market,
                            demo_market=demo_market, notifier=notifier, trader=None,
                            key_problem=problem)

    if not online:
        return offline(OFFLINE)
    if key_loader is None:
        if not keychain.available():
            return offline(NOT_MAC)

        def key_loader() -> Any:
            return load_key(KEYCHAIN_SERVICE_DEMO)

    try:
        key = key_loader()
    except keychain.KeychainError as error:
        return offline(f"Demo anahtarı okunamadı: {error}")
    if key is None:
        return offline(NO_KEY)
    endpoints = endpoints_for(Environment.DEMO)
    budget = RequestBudget(on_ban=lambda text: notifier.send(f"⛔ Demo Mode: {text}",
                                                             kind=KIND_LIMIT))
    trader = DemoTrader(key, environment=Environment.DEMO, budget=budget)
    public = PublicHttp(rest_base=endpoints.rest, retries=1, backoff=1.0, budget=budget,
                        timeout=15.0)

    def user_stream(**kwargs: Any) -> UserStream:
        return UserStream(endpoints.ws_api, key=key, **kwargs)

    def book_stream(on_event: Callable[[Any], None]) -> MarketStream:
        names = [f"{symbol.lower()}@bookTicker" for symbol in symbols]
        return MarketStream(stream_url(endpoints.stream, names), on_event=on_event)

    return DemoExecutor(root, symbols=symbols, engine=engine, live_market=live_market,
                        demo_market=demo_market, notifier=notifier, trader=trader,
                        public=public, stream_factory=user_stream,
                        book_stream_factory=book_stream)


def build_live_executor(
    root: Path | str,
    *,
    symbols: Sequence[str],
    engine: PaperEngine,
    live_market: LiveMarket,
    notifier: Notifier,
    online: bool,
    budget: RequestBudget | None,
    ruleset_loader: Callable[[], RuleSet | None] | None = None,
    key_loader: Callable[[], Any] | None = None,
) -> LiveExecutor:
    """Canlı yürütücü. ``budget``: canlı piyasa istemcisiyle **aynı** istek bütçesi
    (Binance'in ağırlık sınırı IP başınadır; imzalı ve imzasız istekler aynı
    kovadan düşer). Emir defteri ayrı bir akış değil, canlı piyasanın kendisi."""
    root = Path(root)
    symbols = tuple(symbols)

    def offline(problem: str) -> LiveExecutor:
        return LiveExecutor(root, symbols=symbols, engine=engine, live_market=live_market,
                            notifier=notifier, trader=None, key_problem=problem,
                            ruleset_loader=ruleset_loader)

    if not online or budget is None:
        return offline(LIVE_OFFLINE)
    if key_loader is None:
        if not keychain.available():
            return offline(LIVE_NOT_MAC)

        def key_loader() -> Any:
            return load_key(KEYCHAIN_SERVICE_LIVE)

    try:
        key = key_loader()
    except keychain.KeychainError as error:
        return offline(f"Canlı işlem anahtarı okunamadı: {error}")
    if key is None:
        return offline(LIVE.anahtar_yok)
    endpoints = endpoints_for(Environment.LIVE)
    trader = LiveTrader(key, environment=Environment.LIVE, budget=budget)
    public = PublicHttp(rest_base=endpoints.rest, retries=1, backoff=1.0, budget=budget,
                        timeout=15.0)

    def user_stream(**kwargs: Any) -> UserStream:
        return UserStream(endpoints.ws_api, key=key, **kwargs)

    return LiveExecutor(root, symbols=symbols, engine=engine, live_market=live_market,
                        notifier=notifier, trader=trader, public=public,
                        stream_factory=user_stream, ruleset_loader=ruleset_loader)


__all__ = [
    "LIVE_NOT_MAC",
    "LIVE_OFFLINE",
    "NOT_MAC",
    "NO_KEY",
    "OFFLINE",
    "build_executor",
    "build_live_executor",
]
