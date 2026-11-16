"""Gösteri botu: kâğıtta sık işlem yapan, **kural olmayan** bir gösteri.

Berk 16 Kasım 2026'da "günde birkaç işlem yapan botu parasız görmek" istedi
(docs/GOSTERI-BOTU.md). Ölçümler bu sıklıkta kabul edilen bir kural olmadığını
gösterdi; bu modül o sonucu değiştirmez ve bir arama değildir. Tek amacı
mekanizmanın (giriş, hedef, stop, komisyon, risk sınırları) sık işlemde nasıl
davrandığını sahte parayla göstermektir. Beklenen sonuç, zamanla maliyet kadar
zarardır.

Sınırlar (hepsi kodla korunur):

* Yalnızca **Kâğıt İşlem** modundaki coinde çalışır. Demo ve canlı yürütücülere
  hiçbir yoldan ulaşmaz.
* Emirleri ``risk.SOURCE_SHOW`` kaynağıyla yazılır: kural ve elle işlemlerden
  ayrı sayılır, canlıya geçiş kapısına ve kural performansına girmez.
* Kural deposuna (``veri/kurallar.json``) ve birikimli aday sayısına dokunmaz.
* Aynı risk kapılarından geçer; art arda kayıp ya da günlük zarar sınırı
  aşılınca o da durur.
* Varsayılan olarak kapalıdır; arayüzden açılır.

Yöntem bilerek tek ve sabittir, ayarı yoktur (ayar aramak kapanmış aramayı
yeniden açmak olurdu): son kapanmış 1 saatlik mum yükselişle kapandıysa
kapanış fiyatına limit alış; hedef ve stop girişten eşit uzaklıkta (1 saatlik
ATR, en az ``MIN_DISTANCE_PCT``); en fazla ``HOLD_CANDLES`` mum tutulur.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from albsat.core.fees import Side
from albsat.core.filters import SymbolRules

PERIOD = "1h"
#: Hedef ve stopun girişe en küçük uzaklığı (yüzde). ATR bundan küçükse bu kullanılır;
#: böylece hedef, gidiş-dönüş maliyetinin (yaklaşık %0,2-0,3) altına inmez.
MIN_DISTANCE_PCT = Decimal("0.40")
HOLD_CANDLES = 4
VALID_CANDLES = 1
LABEL = "Gösteri botu (kural değil)"

STATE_ENABLED = "gosteri_botu_acik"
STATE_LAST_CANDLE = "gosteri_son_mum_ms:"

NOTE = (
    "Gösteri botu bir kural değildir. Ölçümler bu sıklıkta para kazandıran bir kural "
    "olmadığını gösterdi; bot yalnızca mekanizmayı sahte parayla gösterir. Zamanla "
    "maliyet kadar zarar yazması beklenir. Sonuçları kural ve elle işlemlerden ayrı "
    "sayılır, canlıya geçiş kapısına girmez, gerçek paraya geçemez."
)

ONE_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Signal:
    """Son kapanmış mum yükselişle kapandı."""

    open_time_ms: int
    close_time_ms: int
    close: Decimal


@dataclass(frozen=True)
class Plan:
    giris: Decimal
    hedef: Decimal
    stop: Decimal
    uzaklik_yuzde: Decimal


def signal(frame: pd.DataFrame, *, step_ms: int) -> Signal | None:
    """``frame`` yalnızca kapanmış mumları içermeli (``closed_only``)."""
    if frame.empty:
        return None
    row = frame.iloc[-1]
    open_price = Decimal(str(row["open"]))
    close = Decimal(str(row["close"]))
    if not close > open_price:
        return None
    open_time = int(row["open_time"])
    return Signal(open_time_ms=open_time, close_time_ms=open_time + step_ms, close=close)


def plan(entry: Decimal, atr_pct: Decimal | None, rules: SymbolRules) -> Plan | None:
    """Girişe göre hedef ve stop; borsanın fiyat adımına yuvarlanır."""
    if atr_pct is None or entry <= 0:
        return None
    distance = max(atr_pct, MIN_DISTANCE_PCT)
    giris = rules.round_price(entry, Side.BUY)
    hedef = rules.round_price(giris * (1 + distance / ONE_HUNDRED), Side.SELL)
    stop = rules.round_price(giris * (1 - distance / ONE_HUNDRED), Side.SELL)
    if not hedef > giris > stop > 0:
        return None
    return Plan(giris=giris, hedef=hedef, stop=stop, uzaklik_yuzde=distance)


__all__ = [
    "HOLD_CANDLES",
    "LABEL",
    "MIN_DISTANCE_PCT",
    "NOTE",
    "PERIOD",
    "STATE_ENABLED",
    "STATE_LAST_CANDLE",
    "VALID_CANDLES",
    "Plan",
    "Signal",
    "plan",
    "signal",
]
