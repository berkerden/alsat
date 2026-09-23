"""Faz 4'ün çalışan parçaları: kâğıt motoru, canlı döngü, bildirimler.

Arayüz sunucusu açılırken bir kez kurulur ve kapanırken durdurulur. Testler
aynı nesneyi ağa çıkmadan (``online=False``) kurar; o durumda canlı döngü
yoktur, kâğıt motoru ve bildirimler vardır.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from albsat.core.audit import SOURCE_SYSTEM, SOURCE_TELEGRAM
from albsat.core.clock import iso, utc_now
from albsat.core.filters import SymbolRules
from albsat.data.commission import paper_costs
from albsat.data.exchangeinfo import ExchangeInfoStore
from albsat.data.live import LiveMarket
from albsat.data.store import KlineStore
from albsat.exchange.endpoints import Environment, endpoints_for
from albsat.exchange.http import PublicHttp
from albsat.exchange.ratelimit import RequestBudget
from albsat.modes.state import MODE_PAPER, ModeStore
from albsat.notify.base import KIND_LIMIT, MemoryNotifier
from albsat.paper.engine import PaperEngine
from albsat.paper.fills import PaperCosts
from albsat.paper.runner import LiveRunner
from albsat.strategy import rules as rulestore
from albsat.strategy.rules import RuleSet

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    root: Path
    symbols: tuple[str, ...]
    periods: tuple[str, ...]
    notifier: MemoryNotifier
    engine: PaperEngine
    market: LiveMarket
    runner: LiveRunner | None = None
    telegram: Any = None
    telegram_commands: Any = None
    telegram_note: str = ""
    previous_modes: dict[str, str | None] = field(default_factory=dict)
    started_utc: str = ""

    @classmethod
    def build(
        cls,
        root: Path | str,
        *,
        symbols: Sequence[str],
        periods: Sequence[str],
        online: bool,
        use_telegram: bool = True,
        environment: Environment = Environment.LIVE,
    ) -> Runtime:
        root = Path(root)
        symbols = tuple(symbols)
        notifier, telegram_note = _notifier(root, enabled=use_telegram)
        info_store = ExchangeInfoStore(root)

        def rules_for(symbol: str) -> SymbolRules | None:
            snapshot = info_store.read()
            return snapshot.rules_for(symbol) if snapshot else None

        def costs_for(symbol: str) -> PaperCosts:
            return paper_costs(root, symbol)

        modes = ModeStore.in_directory(root, symbols)
        # SPEC §2: her açılışta Sadece Öneri. Önceki mod yalnızca bilgi olarak kalır.
        started = modes.start_session()
        engine = PaperEngine(root, symbols=symbols, notifier=notifier, costs_for=costs_for,
                             rules_for=rules_for, modes=modes)
        market = LiveMarket(KlineStore(root))
        runtime = cls(
            root=root, symbols=symbols, periods=tuple(periods), notifier=notifier,
            engine=engine, market=market, telegram_note=telegram_note,
            previous_modes={item.sembol: item.onceki_oturum for item in started},
            started_utc=iso(utc_now()),
        )
        engine.audit.write(
            "acilis", "Uygulama açıldı; bütün coinler Sadece Öneri modunda",
            kaynak=SOURCE_SYSTEM,
            ayrinti={"onceki": ",".join(
                f"{item.sembol}={item.onceki_oturum}" for item in started
                if item.onceki_oturum == MODE_PAPER
            )},
        )
        if online:
            endpoints = endpoints_for(environment)
            budget = RequestBudget(on_ban=lambda text: notifier.send(
                f"⛔ {text}", kind=KIND_LIMIT))
            http = PublicHttp(rest_base=endpoints.rest, retries=1, backoff=1.0, budget=budget,
                              timeout=15.0)
            runtime.runner = LiveRunner(
                root, symbols=symbols, periods=periods, engine=engine, market=market,
                http=http, notifier=notifier, stream_base=endpoints.stream,
                ruleset_loader=lambda: _load_rules(root),
            )
        return runtime

    # --- yaşam döngüsü ------------------------------------------------

    def start(self) -> None:
        if self.telegram is not None:
            self.telegram.start()
        if self.telegram_commands is not None:
            self.telegram_commands.start()
        if self.runner is not None:
            self.runner.start()

    def stop(self) -> None:
        if self.runner is not None:
            self.runner.stop()
        if self.telegram_commands is not None:
            self.telegram_commands.stop()
        if self.telegram is not None:
            self.telegram.stop()

    def attach_commands(self) -> None:
        """Telegram komutlarını kâğıt motoruna bağlar (Telegram kuruluysa)."""
        if self.telegram is None:
            return
        from albsat.notify.commands import build_handler
        from albsat.notify.telegram import TelegramCommands

        handler = build_handler(
            self.engine,
            marks=self.marks,
            connection=lambda: self.runner.status() if self.runner else None,
        )

        def ignored(chat_id: int) -> None:
            self.engine.audit.write(
                "telegram_yabanci", "Kayıtlı olmayan bir sohbetten komut geldi; yok sayıldı",
                kaynak=SOURCE_TELEGRAM, ayrinti={"sohbet": chat_id},
            )

        self.telegram_commands = TelegramCommands(
            self.telegram.client, self.telegram.chat_id, handler, on_ignored=ignored
        )

    # --- okuma ---------------------------------------------------------

    def marks(self) -> dict[str, Decimal]:
        if self.runner is not None:
            return self.runner.marks()
        result: dict[str, Decimal] = {}
        for symbol in self.symbols:
            price = self.market.bid(symbol)
            if price is not None:
                result[symbol] = price
        return result

    def telegram_status(self) -> dict[str, Any]:
        if self.telegram is None:
            return {"kurulu": False, "aciklama": self.telegram_note}
        status: dict[str, Any] = dict(self.telegram.status())
        status["komutlar"] = None if self.telegram_commands is None else {
            "yanitlanan": self.telegram_commands.handled,
            "yok_sayilan": self.telegram_commands.ignored,
            "son_hata": self.telegram_commands.last_error,
        }
        return status


def _load_rules(root: Path) -> RuleSet | None:
    path = rulestore.path_for(root)
    if not path.exists():
        return None
    return rulestore.load(path)


def _notifier(root: Path, *, enabled: bool) -> tuple[MemoryNotifier, str]:
    if not enabled:
        return MemoryNotifier(), "Telegram bu çalıştırmada kapalı."
    from albsat.core import keychain
    from albsat.notify.telegram import (
        TelegramClient,
        TelegramConfig,
        TelegramNotifier,
        load_token,
    )

    config = TelegramConfig.load(root)
    if config is None:
        return MemoryNotifier(), (
            "Telegram kurulu değil. Bildirimler yine üretilir ve burada görünür; telefona "
            "gelmesi için: bash kurulum.sh telegram"
        )
    if not keychain.available():
        return MemoryNotifier(), "Telegram jetonu yalnızca macOS Anahtar Zinciri'nden okunur."
    token = load_token()
    if token is None:
        return MemoryNotifier(), (
            "telegram.json var ama jeton Anahtar Zinciri'nde bulunamadı. Yeniden kurun: "
            "bash kurulum.sh telegram"
        )
    notifier = TelegramNotifier(TelegramClient(token), config.chat_id,
                                bot_name=config.bot_kullanici_adi)
    return notifier, f"@{config.bot_kullanici_adi} üzerinden gönderiliyor."


def attach_telegram(runtime: Runtime) -> None:
    """Bildirici Telegram ise komut dinleyicisini kurar."""
    from albsat.notify.telegram import TelegramNotifier

    if isinstance(runtime.notifier, TelegramNotifier):
        runtime.telegram = runtime.notifier
        runtime.attach_commands()


__all__ = ["Runtime", "attach_telegram"]
