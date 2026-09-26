"""Çalışan parçalar: kâğıt motoru, canlı döngü, bildirimler, Demo ve canlı yürütücüler.

Faz 7'den beri iki parça daha: günlük yedek (``core.backup``) ve dış gözcü
(``notify.gozcu``; uygulama kapanınca ya da takılınca alarm).

Arayüz sunucusu açılırken bir kez kurulur ve kapanırken durdurulur. Testler
aynı nesneyi ağa çıkmadan (``online=False``) kurar; o durumda canlı döngü
yoktur, kâğıt motoru ve bildirimler vardır. Demo (Faz 5) ve canlı (Faz 6)
yürütücüler her zaman kurulur; ağ ya da anahtar yoksa emir gönderemez ve
nedenini söyler.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from albsat.core import keychain
from albsat.core.audit import SOURCE_SYSTEM, SOURCE_TELEGRAM
from albsat.core.backup import BackupScheduler
from albsat.core.clock import iso, utc_now
from albsat.core.filters import SymbolRules
from albsat.data.commission import paper_costs
from albsat.data.exchangeinfo import ExchangeInfoStore
from albsat.data.live import LiveMarket
from albsat.data.store import KlineStore
from albsat.exchange.endpoints import Environment, endpoints_for
from albsat.exchange.http import PublicHttp
from albsat.exchange.ratelimit import RequestBudget
from albsat.execution.executor import DemoExecutor
from albsat.execution.live import LiveExecutor
from albsat.execution.service import build_executor, build_live_executor
from albsat.modes.state import MODE_PAPER, ModeStore
from albsat.notify.base import KIND_LIMIT, KIND_SYSTEM, MemoryNotifier
from albsat.notify.gozcu import HealthVerdict
from albsat.paper.engine import PaperEngine
from albsat.paper.fills import PaperCosts
from albsat.paper.runner import LiveRunner
from albsat.strategy import rules as rulestore
from albsat.strategy.rules import RuleSet

logger = logging.getLogger(__name__)

#: Gözcü: canlı döngü bu kadar saniye ilerlemezse "takıldı" sayılır.
LOOP_STALL_SECONDS = 180.0
#: Gözcü: bir coinin fiyatı bu kadar saniyeden eskiyse veri "bayat" sayılır.
DATA_STALE_SECONDS = 300.0
#: Gözcü: diskte bundan az boş yer kalırsa sorun sayılır (SQLite yazamaz olur).
MIN_FREE_BYTES = 200 * 1024 * 1024


@dataclass
class Runtime:
    root: Path
    symbols: tuple[str, ...]
    periods: tuple[str, ...]
    notifier: MemoryNotifier
    engine: PaperEngine
    market: LiveMarket
    runner: LiveRunner | None = None
    demo: DemoExecutor | None = None
    live: LiveExecutor | None = None
    telegram: Any = None
    telegram_commands: Any = None
    telegram_note: str = ""
    previous_modes: dict[str, str | None] = field(default_factory=dict)
    started_utc: str = ""
    #: Faz 7: günlük yedek (``core.backup.BackupScheduler``).
    backups: BackupScheduler | None = None
    #: Faz 7: dış gözcü (``notify.gozcu.Watchdog``); kurulu değilse ``None``.
    watchdog: Any = None
    watchdog_note: str = ""

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
        use_demo: bool = True,
        demo_key_loader: Any = None,
        use_live: bool = True,
        live_key_loader: Any = None,
        use_backup: bool = True,
        use_watchdog: bool = True,
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
        budget: RequestBudget | None = None
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
        runtime.demo = build_executor(
            root, symbols=symbols, engine=engine, live_market=market, notifier=notifier,
            online=online and use_demo, key_loader=demo_key_loader,
        )
        # Canlı hesap yalnızca canlı piyasayla (``Environment.LIVE``) açılır ve canlı
        # piyasa istemcisinin istek bütçesini paylaşır (ağırlık sınırı IP başına).
        live_online = online and use_live and environment == Environment.LIVE
        runtime.live = build_live_executor(
            root, symbols=symbols, engine=engine, live_market=market, notifier=notifier,
            online=live_online, budget=budget if live_online else None,
            ruleset_loader=lambda: _load_rules(root), key_loader=live_key_loader,
        )
        if runtime.runner is not None:
            runtime.runner.demo = runtime.demo
            runtime.runner.live = runtime.live
        if use_backup:
            runtime.backups = BackupScheduler(
                root, on_failure=lambda text: notifier.send(text, kind=KIND_SYSTEM)
            )
        if online and use_watchdog:
            runtime.watchdog, runtime.watchdog_note = _watchdog(runtime)
        else:
            runtime.watchdog_note = "Gözcü bu çalıştırmada kapalı (ağ yok)."
        return runtime

    # --- yaşam döngüsü ------------------------------------------------

    def start(self) -> None:
        if self.telegram is not None:
            self.telegram.start()
        if self.telegram_commands is not None:
            self.telegram_commands.start()
        if self.runner is not None:
            self.runner.start()
        if self.demo is not None:
            self.demo.start()
        if self.live is not None:
            self.live.start()
        if self.backups is not None:
            self.backups.start()
        if self.watchdog is not None:
            self.watchdog.start()

    def stop(self) -> None:
        if self.watchdog is not None:
            # Gözcüye not: alarm yine gelir (uygulama gerçekten kapalı), ama
            # ayrıntısında bunun bilerek kapatma olduğu görünür.
            self.watchdog.stop(note="Uygulama kapatıldı (elle ya da yeniden başlatma).")
        if self.backups is not None:
            self.backups.stop()
        if self.live is not None:
            self.live.stop()
        if self.demo is not None:
            self.demo.stop()
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
            demo=self.demo,
            demo_marks=self.demo_marks,
            live=self.live,
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

    def demo_marks(self) -> dict[str, Decimal]:
        """Demo pozisyonlarının değerlemesi: Demo defterinin en iyi alışı (canlıdan ayrı)."""
        result = self.marks()
        if self.demo is not None:
            for symbol in self.symbols:
                quote = self.demo.demo_market.quote(symbol)
                if quote is not None:
                    result[symbol] = quote.alis
        return result

    def kill_switch(self, *, close_positions: bool, source: str) -> dict[str, int]:
        """ACİL DURDUR: önce kâğıt motoru (modları indirir, yürütücülere iptal isteği
        bırakır), sonra canlı ve Demo yürütücüleri (borsadaki bekleyen girişleri iptal
        eder, istenirse korumalı satışla kapatır)."""
        events = self.engine.kill_switch(close_positions=close_positions, marks=self.marks(),
                                         source=source, now=utc_now())
        live_closing = 0
        if self.live is not None:
            live_closing = self.live.kill_switch(close_positions=close_positions, source=source)
        demo_closing = 0
        if self.demo is not None:
            demo_closing = self.demo.kill_switch(close_positions=close_positions, source=source)
        return {"iptal_edilen": len(events.iptal), "kapatilan": len(events.kapanan),
                "demo_kapatilan": demo_closing, "canli_kapatilan": live_closing}

    def health_verdict(self) -> HealthVerdict:
        """Gözcü için tek cümlelik sağlık kararı (``notify.gozcu``)."""
        problems: list[str] = []
        runner = self.runner
        if runner is not None:
            age = runner.loop_age()
            if age is not None and age > LOOP_STALL_SECONDS:
                problems.append(f"canlı döngü {age:.0f} sn'dir ilerlemiyor")
            stale = runner.stale_symbols(DATA_STALE_SECONDS)
            if stale:
                problems.append(f"piyasa verisi gelmiyor (son {DATA_STALE_SECONDS / 60:.0f} "
                                f"dakikada fiyat yok): {', '.join(stale)}")
        try:
            free = shutil.disk_usage(self.root).free
        except OSError as error:
            problems.append(f"disk okunamadı: {error.strerror}")
        else:
            if free < MIN_FREE_BYTES:
                problems.append(f"diskte {free // (1024 * 1024)} MB boş yer kaldı")
        return HealthVerdict(not problems, "; ".join(problems) or "sağlıklı")

    def watchdog_status(self) -> dict[str, Any]:
        if self.watchdog is None:
            return {"kurulu": False, "aciklama": self.watchdog_note}
        status: dict[str, Any] = dict(self.watchdog.status())
        status["aciklama"] = self.watchdog_note
        return status

    def backup_status(self) -> dict[str, Any] | None:
        return None if self.backups is None else self.backups.status()

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
        return MemoryNotifier(), "Telegram jetonu okunamıyor: bu bilgisayarda sır deposu yok."
    try:
        token = load_token()
    except keychain.KeychainError as error:
        return MemoryNotifier(), f"Telegram jetonu okunamadı: {error}"
    if token is None:
        return MemoryNotifier(), (
            f"telegram.json var ama jeton {keychain.where('de')} bulunamadı. Yeniden kurun: "
            "bash kurulum.sh telegram"
        )
    notifier = TelegramNotifier(TelegramClient(token), config.chat_id,
                                bot_name=config.bot_kullanici_adi)
    return notifier, f"@{config.bot_kullanici_adi} üzerinden gönderiliyor."


def _watchdog(runtime: Runtime) -> tuple[Any, str]:
    from albsat.notify import gozcu

    if not keychain.available():
        return None, "Gözcü adresi okunamıyor: bu bilgisayarda sır deposu yok."
    try:
        url = gozcu.load_url_strict()
    except keychain.KeychainError as error:
        return None, f"Gözcü adresi okunamadı: {error}"
    if url is None:
        return None, ("Gözcü kurulu değil: uygulama kapanınca ya da takılınca haber veren alarm "
                      "yok. Kurmak için: bash kurulum.sh gozcu")
    try:
        client = gozcu.PingClient(url)
    except gozcu.WatchdogError as error:
        return None, str(error)
    watchdog = gozcu.Watchdog(client, check=runtime.health_verdict, notifier=runtime.notifier)
    minutes = gozcu.INTERVAL_SECONDS / 60
    return watchdog, f"{client.host} ({minutes:.0f} dakikada bir 'çalışıyorum')"


def attach_telegram(runtime: Runtime) -> None:
    """Bildirici Telegram ise komut dinleyicisini kurar."""
    from albsat.notify.telegram import TelegramNotifier

    if isinstance(runtime.notifier, TelegramNotifier):
        runtime.telegram = runtime.notifier
        runtime.attach_commands()


__all__ = ["Runtime", "attach_telegram"]
