"""Günlük trend durumu göstergesi (docs/TREND-SONUC.md §6-§7).

Günlük trend testinde (docs/TREND-ONKAYIT.md) on sınamadan yalnızca biri
geçti: SOLUSDT'de 20/10 gün kırılımı. Ön kayıt §8'e göre kural parayla
kullanılmaz; Berk 19 Ekim 2026'da arayüzde bilgi göstergesi olarak
görmek istedi. Gösterge **yalnızca bilgi verir**: emir göndermez, öneri
kartı üretmez, kâğıt işleme ya da sinyal günlüğüne bağlı değildir.

Kural sınamadakiyle aynı koddur (``trend.RULES``); burada yeniden yazılmaz.
Günlük mumlar ``veri/klines/SOLUSDT/1d`` dosyasından okunur. Uygulama açıkken
canlı döngü yeni kapanan günlük mumu kendiliğinden ekler
(``paper.runner.LiveRunner.sync_trend``).

**Sınamadan sonrası.** Sınamanın gördüğü son günlük mum 2026-10-18'dir.
Ondan sonraki günler kural için yeni veridir; kuralın gerçekten işe yarayıp
yaramadığını en dürüst gösterecek olan bu dönemdir. Gösterge bu dönemde
kuralın ve al-ve-tut'un sonucunu, sınamadaki maliyetle ayrıca yazar.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from albsat.data.klines import closed_only, interval_ms
from albsat.research import trend

SYMBOL = "SOLUSDT"
INTERVAL = "1d"
RULE_KEY = "kirilim_20_10"
DAY_MS = interval_ms(INTERVAL)

#: Sınamanın gördüğü son günlük mumun açılışı (2026-10-18, UTC).
TEST_LAST_OPEN_MS = int(dt.datetime(2026, 10, 18, tzinfo=dt.UTC).timestamp() * 1000)
TEST_DATE_TR = "19 Ekim 2026"

#: Sınamadaki tek yön maliyeti (komisyon %0,1 + spread, kayma, güvenlik payı).
COST_PER_SIDE = 0.0014

#: Durumun güvenilir olması için gereken en az günlük mum. Kırılım kuralının
#: durumu geçmişe bağlıdır; ilk al ya da sat sinyalinden sonra geçmişin
#: başlangıcından bağımsızlaşır. 120 gün içinde bu kural pratikte her zaman
#: en az bir sinyal verir; daha kısa veride durum gösterilmez.
MIN_ROWS = 120

#: Son kapanmış günlük mum bundan eskiyse veri "güncel değil" sayılır.
STALE_AFTER_DAYS = 2

HOLD = "elde_tut"
CASH = "nakitte_bekle"
STATE_TR = {HOLD: "Elde tut", CASH: "Nakitte bekle"}

NOTE = (
    "Günlük trend testinde (19 Ekim 2026) on sınamadan geçen tek kural; kıl payı "
    "geçti (q = 0,094). Geçmişte işe yaradı, geleceği garanti etmez. Bilgi verir, "
    "öneri değildir ve emir göndermez."
)


def _rule() -> trend.TrendRule:
    return next(rule for rule in trend.RULES if rule.key == RULE_KEY)


def _date(open_time_ms: int) -> str:
    return dt.datetime.fromtimestamp(open_time_ms / 1000, tz=dt.UTC).date().isoformat()


def price_text(value: float) -> str:
    """Fiyatı ekranda okunur basamakla yazar (hesap için kullanılmaz)."""
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.6f}"


@dataclass(frozen=True)
class SinceTest:
    """Sınamanın görmediği dönemde kural ve al-ve-tut (yüzde, maliyet sonrası)."""

    days: int
    rule_pct: float
    hold_pct: float
    switches: int


@dataclass(frozen=True)
class TrendStatus:
    symbol: str
    rule: trend.TrendRule
    state: str | None = None
    last_open_ms: int | None = None
    last_close: float | None = None
    since_open_ms: int | None = None
    #: Nakitteyken: sonraki kapanış bunu aşarsa al. Eldeyken: altına inerse sat.
    trigger: float | None = None
    since_test: SinceTest | None = None
    stale_days: int | None = None
    problem: str | None = None


def evaluate(frame: pd.DataFrame, *, now_ms: int, symbol: str = SYMBOL) -> TrendStatus:
    """Günlük mumlardan kuralın bugünkü durumu."""
    rule = _rule()
    if frame.empty:
        return TrendStatus(symbol, rule, problem=(
            f"{symbol} günlük verisi yok. Uygulama açıkken internete bağlanınca "
            "kendiliğinden iner."
        ))
    frame = closed_only(frame).sort_values("open_time").reset_index(drop=True)
    if len(frame) < MIN_ROWS:
        return TrendStatus(symbol, rule, problem=(
            f"{symbol} için {len(frame)} günlük mum var; durum için en az {MIN_ROWS} gerekir."
        ))

    close = frame["close"].to_numpy(dtype=float)
    times = frame["open_time"].to_numpy(dtype="int64")
    positions = rule.positions(frame)
    holding = bool(positions[-1])

    changes = np.flatnonzero(positions[1:] != positions[:-1]) + 1
    since = int(times[changes[-1]]) if changes.size else None
    # Eldeyken: son 10 günün en düşük kapanışı (altına inerse sat).
    # Nakitteyken: son 20 günün en yüksek kapanışı (aşarsa al).
    trigger = float(close[-10:].min()) if holding else float(close[-20:].max())

    last_open = int(times[-1])
    stale_days = max(0, int((now_ms - (last_open + DAY_MS)) // DAY_MS))

    return TrendStatus(
        symbol, rule,
        state=HOLD if holding else CASH,
        last_open_ms=last_open,
        last_close=float(close[-1]),
        since_open_ms=since,
        trigger=trigger,
        since_test=_since_test(times, close, positions),
        stale_days=stale_days,
    )


def _since_test(times: np.ndarray, close: np.ndarray, positions: np.ndarray) -> SinceTest | None:
    """Sınamanın son mumundan bu yana; yeni kapanmış mum yoksa ``None``."""
    matches = np.flatnonzero(times == TEST_LAST_OPEN_MS)
    if matches.size == 0:
        return None
    start = int(matches[0])
    if start >= close.size - 1:
        return None
    returns = close[start + 1 :] / close[start:-1] - 1.0
    held = positions[start:-1].astype(bool)
    rule_growth = float(np.prod(trend.net_growth(held, returns, COST_PER_SIDE)))
    hold_growth = float(np.prod(trend.net_growth(np.ones_like(held), returns, COST_PER_SIDE)))
    return SinceTest(
        days=int(returns.size),
        rule_pct=(rule_growth - 1.0) * 100.0,
        hold_pct=(hold_growth - 1.0) * 100.0,
        switches=int(np.count_nonzero(positions[start + 1 :] != positions[start:-1])),
    )


def to_json(status: TrendStatus, *, live: bool) -> dict[str, Any]:
    """Arayüzün okuduğu biçim. Fiyatlar metin, tarihler UTC günü."""
    rule = status.rule
    data: dict[str, Any] = {
        "sembol": status.symbol,
        "kural_ad": rule.name_tr,
        "kural_tanim": rule.rule_tr,
        "not": NOTE,
        "canli": live,
        "sorun": status.problem,
        "durum": status.state,
        "durum_tr": STATE_TR.get(status.state or ""),
        "son_gun": None if status.last_open_ms is None else _date(status.last_open_ms),
        "son_kapanis": None if status.last_close is None else price_text(status.last_close),
        "degisim_gunu": None if status.since_open_ms is None else _date(status.since_open_ms),
        "esik": None if status.trigger is None else price_text(status.trigger),
        "esik_tr": None,
        "bayat_gun": status.stale_days,
        "bayat": status.stale_days is not None and status.stale_days >= STALE_AFTER_DAYS,
        "sinama_tarihi": TEST_DATE_TR,
        "sinamadan_sonra": None,
    }
    if status.state == HOLD:
        data["esik_tr"] = "Satış eşiği: bir günlük kapanış bunun altına inerse sat"
    elif status.state == CASH:
        data["esik_tr"] = "Alım eşiği: bir günlük kapanış bunu aşarsa al"
    if status.since_test is not None:
        data["sinamadan_sonra"] = {
            "gun": status.since_test.days,
            "kural_yuzde": round(status.since_test.rule_pct, 2),
            "al_ve_tut_yuzde": round(status.since_test.hold_pct, 2),
            "degisim": status.since_test.switches,
        }
    return data


__all__ = [
    "CASH",
    "COST_PER_SIDE",
    "HOLD",
    "INTERVAL",
    "SYMBOL",
    "TEST_LAST_OPEN_MS",
    "SinceTest",
    "TrendStatus",
    "evaluate",
    "price_text",
    "to_json",
]
