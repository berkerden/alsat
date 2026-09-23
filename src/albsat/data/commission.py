"""Ölçülen hesap komisyonu (``veri/komisyon.json``) ve kâğıt işlemin maliyetleri.

Komisyon oranı koda sabit yazılmaz (SPEC §11). Hesaba özel oran
``GET /api/v3/account/commission`` ile ölçülüp buraya yazılır; ölçülmediyse
Binance'in genel standart oranı (%0,1) **varsayım** olarak kullanılır ve
arayüzde öyle yazılır.

Kayma yüzdesi ``config/default.yaml`` → ``maliyet.beklenen_kayma_yuzde``
ile aynıdır; test ikisinin ayrışmadığını denetler.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from albsat.core.clock import iso, istanbul_text, utc_now
from albsat.core.fees import CommissionTable, Liquidity, Side, flat_table
from albsat.paper.fills import PaperCosts

FILENAME = "komisyon.json"
#: Ölçülmediyse kullanılan varsayım: Binance genel standart oranı.
ASSUMED_RATE = "0.001"
DEFAULT_SLIPPAGE_PCT = Decimal("0.02")


@dataclass(frozen=True)
class MeasuredCommissions:
    olcum_utc: str
    tablolar: dict[str, CommissionTable]
    ham: dict[str, Any]

    def rates_text(self, symbol: str) -> str:
        table = self.tablolar.get(symbol)
        if table is None:
            return "ölçülmedi"
        maker = table.effective_rate_pct(Side.BUY, Liquidity.MAKER)
        taker = table.effective_rate_pct(Side.BUY, Liquidity.TAKER)
        return f"maker %{maker.normalize()}, taker %{taker.normalize()}"


class CommissionStore:
    def __init__(self, root: Path | str) -> None:
        self.path = Path(root) / FILENAME

    def write(self, payloads: Mapping[str, Mapping[str, Any]]) -> Path:
        document = {
            "olcum_utc": iso(utc_now()),
            "kaynak": "GET /api/v3/account/commission",
            "semboller": {symbol: dict(payload) for symbol, payload in payloads.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(self.path)
        return self.path

    def read(self) -> MeasuredCommissions | None:
        if not self.path.exists():
            return None
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            raw = dict(document["semboller"])
            tables = {symbol: CommissionTable.from_api(payload) for symbol, payload in raw.items()}
            return MeasuredCommissions(str(document.get("olcum_utc", "")), tables, raw)
        except (OSError, ValueError, KeyError, TypeError):
            return None


def paper_costs(root: Path | str, symbol: str,
                slippage_pct: Decimal = DEFAULT_SLIPPAGE_PCT) -> PaperCosts:
    measured = CommissionStore(root).read()
    table = measured.tablolar.get(symbol) if measured else None
    if measured is not None and table is not None:
        return PaperCosts(
            table, slippage_pct,
            f"hesabınızdan ölçülen komisyon ({istanbul_text(measured.olcum_utc)}): "
            f"{measured.rates_text(symbol)}; kayma %{slippage_pct}",
        )
    return PaperCosts(
        flat_table(symbol, ASSUMED_RATE, ASSUMED_RATE), slippage_pct,
        "varsayım: Binance standart komisyonu %0.1 (hesabınıza özel oran henüz "
        f"ölçülmedi); kayma %{slippage_pct}",
    )


def research_rates(root: Path | str) -> tuple[str, str] | None:
    """Araştırma için (maker, taker): ölçülen sembollerin en yükseği (temkinli)."""
    measured = CommissionStore(root).read()
    if measured is None or not measured.tablolar:
        return None
    tables = measured.tablolar.values()
    makers = [table.effective_rate(side, Liquidity.MAKER) for table in tables
              for side in (Side.BUY, Side.SELL)]
    takers = [table.effective_rate(side, Liquidity.TAKER) for table in tables
              for side in (Side.BUY, Side.SELL)]
    return str(max(makers)), str(max(takers))


__all__ = [
    "ASSUMED_RATE",
    "DEFAULT_SLIPPAGE_PCT",
    "CommissionStore",
    "MeasuredCommissions",
    "paper_costs",
    "research_rates",
]
