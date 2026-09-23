"""Demo yürütücüsünün kurulumu: anahtar, istemci, akışlar.

Anahtar yalnızca burada, macOS Anahtar Zinciri'nden okunur
(``albsat-binance-demo`` kaydı). Arayüz katmanı anahtara ve imzalı istemciye
hiç dokunmaz; yalnızca kurulmuş yürütücüyle konuşur.

Anahtar yoksa (ya da bilgisayar Mac değilse) yürütücü yine kurulur ama
``trader`` boştur: Demo sekmesi açılır, Demo Mode seçilemez ve nedeni
yazılır.
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
from albsat.exchange.trading import KEYCHAIN_SERVICE_DEMO, DemoTrader
from albsat.exchange.user_stream import UserStream
from albsat.execution.executor import DemoExecutor
from albsat.notify.base import KIND_LIMIT, Notifier
from albsat.paper.engine import PaperEngine

NO_KEY = ("Demo Mode anahtarı kurulu değil. Kurmak için Terminal'de: "
          "bash kurulum.sh demo-anahtar")
NOT_MAC = "Demo anahtarı yalnızca macOS Anahtar Zinciri'nden okunur."
OFFLINE = "Bu çalıştırmada ağ kapalı; Demo Mode kullanılamaz."


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

    key = key_loader()
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


__all__ = ["NOT_MAC", "NO_KEY", "OFFLINE", "build_executor"]
