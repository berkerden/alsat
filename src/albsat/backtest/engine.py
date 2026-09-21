"""Olay tabanlı backtest motoru (SPEC.md §4.8, Faz 2 kabul kriteri).

Olay çalışması "her mumda girseydik ne olurdu" sorusunu cevaplar ve aynı
hareketi üst üste binen pencerelerle birden çok kez sayar. Backtest farklı
bir soru sorar: **tek bir hesapla, sırayla işlem yapsaydık ne olurdu?**

Fark önemlidir. Bir örüntü günde on kez tetiklenebilir ama pozisyon açıkken
gelen sinyal alınamaz; aynı anda en fazla ``max_positions`` pozisyon olur
(varsayılan 1, ``config/default.yaml`` ile aynı). Bu yüzden backtest'in
işlem sayısı olay sayısından her zaman azdır ve gerçeğe daha yakındır.

Motor, olay tablosunu (``albsat.research.eventstudy``) girdi alır: hangi
mumda girilir, kaç mum sonra ve hangi sebeple çıkılır, net yüzde nedir —
hepsi orada hesaplanmıştır. Burada yalnızca sıralama ve sermaye yönetimi var.
Tek doğruluk kaynağı ilkesi: çıkış kuralları iki yerde ayrı ayrı
yazılmaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from albsat.research.eventstudy import EXIT_LABELS_TR, EXIT_TARGET, OutcomeTable
from albsat.research.stats import max_drawdown_pct, sharpe_ratio

#: Bir yıldaki milisaniye (yıllıklandırma için).
MS_PER_YEAR = 365.25 * 24 * 3600 * 1000


@dataclass(frozen=True)
class Trade:
    """Tek bir işlem."""

    entry_index: int
    exit_index: int
    entry_time: int
    entry_price: float
    exit_price: float
    net_pct: float
    exit_reason: int
    bars_held: int

    @property
    def reason_tr(self) -> str:
        return EXIT_LABELS_TR.get(self.exit_reason, "?")


@dataclass(frozen=True)
class BacktestResult:
    """Bir backtest çalıştırmasının sonucu."""

    trades: tuple[Trade, ...]
    equity: np.ndarray
    start_equity: float
    span_ms: int
    signals_seen: int = 0
    signals_skipped_busy: int = 0

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def net_returns(self) -> np.ndarray:
        return np.array([trade.net_pct for trade in self.trades], dtype=float)

    @property
    def total_return_pct(self) -> float:
        if self.equity.size == 0:
            return 0.0
        return float(self.equity[-1] / self.start_equity - 1.0) * 100.0

    @property
    def hit_rate(self) -> float:
        if not self.trades:
            return 0.0
        return float(np.mean([t.exit_reason == EXIT_TARGET for t in self.trades]))

    @property
    def expectancy_pct(self) -> float:
        returns = self.net_returns
        return float(returns.mean()) if returns.size else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        return max_drawdown_pct(self.equity) if self.equity.size else 0.0

    @property
    def profit_factor(self) -> float:
        """Kazançların toplamı / kayıpların toplamı. 1'in altı zarar demektir."""
        returns = self.net_returns
        if returns.size == 0:
            return 0.0
        gains = returns[returns > 0].sum()
        losses = -returns[returns < 0].sum()
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return float(gains / losses)

    @property
    def trades_per_year(self) -> float:
        if self.span_ms <= 0:
            return 0.0
        return self.trade_count * MS_PER_YEAR / self.span_ms

    @property
    def annual_sharpe(self) -> float:
        return sharpe_ratio(self.net_returns, periods_per_year=self.trades_per_year)

    def trade_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "giris_zamani": pd.to_datetime(t.entry_time, unit="ms", utc=True),
                    "giris": t.entry_price,
                    "cikis": t.exit_price,
                    "net%": t.net_pct,
                    "sebep": t.reason_tr,
                    "mum": t.bars_held,
                }
                for t in self.trades
            ]
        )


def run(
    frame: pd.DataFrame,
    outcomes: OutcomeTable,
    signals: np.ndarray,
    *,
    start_equity: float = 100.0,
    max_positions: int = 1,
    cooldown_bars: int = 0,
) -> BacktestResult:
    """Sinyalleri sırayla işler; pozisyon açıkken gelen sinyali atlar.

    ``cooldown_bars``: zararla kapanan işlemden sonra kaç mum beklenecek
    (``config/default.yaml`` → ``risk.kayip_sonrasi_soguma_mum``). Varsayılan
    0; risk motoru Faz 4'te bu değeri kendisi verecek.
    """
    if max_positions != 1:
        raise NotImplementedError(
            "Şu an aynı anda tek pozisyon destekleniyor; "
            "çoklu pozisyon risk motoruyla birlikte (Faz 4) gelecek."
        )

    mask = np.asarray(signals, dtype=bool) & outcomes.eligible
    open_time = frame["open_time"].to_numpy()
    count = int(len(frame))

    trades: list[Trade] = []
    equity_points = [start_equity]
    equity = start_equity
    free_from = 0
    seen = 0
    skipped = 0

    for position in np.flatnonzero(mask):
        index = int(position)
        seen += 1
        if index < free_from:
            skipped += 1
            continue

        held = int(outcomes.bars_held[index])
        net = float(outcomes.net_pct[index])
        if not np.isfinite(net):
            continue

        exit_index = min(index + held, count - 1)
        equity *= 1.0 + net / 100.0
        equity_points.append(equity)
        trades.append(
            Trade(
                entry_index=index,
                exit_index=exit_index,
                entry_time=int(open_time[index]),
                entry_price=float(outcomes.entry_price[index]),
                exit_price=float(outcomes.exit_price[index]),
                net_pct=net,
                exit_reason=int(outcomes.exit_reason[index]),
                bars_held=held,
            )
        )
        # Çıkış, ``index + held`` numaralı mumun içinde olur. Bir sonraki
        # sinyal en erken o mumda verilebilir, girişi de ondan sonraki mumda.
        free_from = index + held
        if net < 0 and cooldown_bars:
            free_from += cooldown_bars

    span = int(open_time[-1] - open_time[0]) if count > 1 else 0
    return BacktestResult(
        trades=tuple(trades),
        equity=np.array(equity_points, dtype=float),
        start_equity=start_equity,
        span_ms=span,
        signals_seen=seen,
        signals_skipped_busy=skipped,
    )


@dataclass(frozen=True)
class ResultSummary:
    """Raporda gösterilecek özet satırı."""

    label: str
    trades: int
    total_return_pct: float
    expectancy_pct: float
    hit_rate: float
    max_drawdown_pct: float
    annual_sharpe: float
    profit_factor: float
    extras: dict[str, float] = field(default_factory=dict)


def summarize(result: BacktestResult, label: str) -> ResultSummary:
    return ResultSummary(
        label=label,
        trades=result.trade_count,
        total_return_pct=result.total_return_pct,
        expectancy_pct=result.expectancy_pct,
        hit_rate=result.hit_rate,
        max_drawdown_pct=result.max_drawdown_pct,
        annual_sharpe=result.annual_sharpe,
        profit_factor=result.profit_factor,
    )
