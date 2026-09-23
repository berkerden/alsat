"""Canlı piyasa durumu: akıştan gelen fiyatlar ve risk filtrelerinin ölçümleri.

``MarketStream`` (ya da yedek REST yoklaması) olayları buraya yazar; risk
motoru karar anında ``market_state`` ile bir ``MarketState`` alır. Bu sınıf
ağa çıkmaz, yalnızca kendisine verileni saklar ve ölçer.

Ölçümler:

* **Spread**: en iyi alış/satış farkı, orta fiyata oranla yüzde. En fazla
  saniyede bir örnek alınır, son bir saatin medyanı "normal" sayılır. Bir
  saatten az gözlem varsa (en az 5 dakika) medyan yine hesaplanır; hiç yoksa
  filtre "ölçülemedi" der.
* **Volatilite**: işlem periyodundaki güncel ATR(14) yüzdesi ve son 30 günün
  medyanı, disk deposundaki mumlardan.
* **BTC sert hareket**: son 60 adet 1 dakikalık BTC mumunda, ilk mumun
  açılışına göre en büyük mutlak sapma.
* **Hacim**: borsanın 24 saatlik USDT hacmi (``miniTicker``).
"""

from __future__ import annotations

import statistics
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import numpy as np

from albsat.core.clock import utc_now
from albsat.core.filters import SymbolRules
from albsat.core.money import ONE_HUNDRED, ZERO
from albsat.data.klines import closed_only, interval_ms
from albsat.data.store import KlineStore
from albsat.paper.fills import Candle
from albsat.risk.market import MarketState, spread_pct

BTC = "BTCUSDT"
USDTTRY = "USDTTRY"

#: Spread örnekleme aralığı ve penceresi.
SPREAD_SAMPLE_SECONDS = 1.0
SPREAD_WINDOW = 3600
SPREAD_MIN_SAMPLES = 300

ATR_PERIOD = 14
ATR_MEDIAN_DAYS = 30


@dataclass(frozen=True)
class Quote:
    alis: Decimal
    satis: Decimal
    zaman: datetime

    @property
    def orta(self) -> Decimal:
        return (self.alis + self.satis) / 2


class LiveMarket:
    def __init__(
        self,
        store: KlineStore,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.clock = clock
        self._lock = threading.Lock()
        self._quotes: dict[str, Quote] = {}
        self._spreads: dict[str, deque[float]] = {}
        self._last_sample: dict[str, datetime] = {}
        self._last_price: dict[str, Decimal] = {}
        self._volume: dict[str, Decimal] = {}
        self._last_event: dict[str, datetime] = {}
        self._minutes: dict[str, deque[Candle]] = {}
        self._atr_cache: dict[tuple[str, str], tuple[int, Decimal | None, Decimal | None]] = {}

    # --- olaylar -------------------------------------------------------

    def on_book(self, sembol: str, alis: Decimal, satis: Decimal) -> None:
        now = self.clock()
        with self._lock:
            self._quotes[sembol] = Quote(alis, satis, now)
            self._last_event[sembol] = now
            last = self._last_sample.get(sembol)
            if last is None or (now - last).total_seconds() >= SPREAD_SAMPLE_SECONDS:
                value = spread_pct(alis, satis)
                if value is not None:
                    self._spreads.setdefault(sembol, deque(maxlen=SPREAD_WINDOW)).append(
                        float(value)
                    )
                    self._last_sample[sembol] = now

    def on_ticker(self, sembol: str, son_fiyat: Decimal, hacim_quote_24s: Decimal) -> None:
        now = self.clock()
        with self._lock:
            self._last_price[sembol] = son_fiyat
            self._volume[sembol] = hacim_quote_24s
            self._last_event[sembol] = now

    def on_minute(self, sembol: str, candle: Candle) -> None:
        """Kapanmış 1 dakikalık mum."""
        with self._lock:
            window = self._minutes.setdefault(sembol, deque(maxlen=60))
            if window and candle.open_time_ms <= window[-1].open_time_ms:
                return
            window.append(candle)
            self._last_price.setdefault(sembol, candle.close)
            self._last_event[sembol] = max(
                self._last_event.get(sembol, self.clock()), self.clock()
            )

    # --- okuma ---------------------------------------------------------

    def quote(self, sembol: str) -> Quote | None:
        with self._lock:
            return self._quotes.get(sembol)

    def last_price(self, sembol: str) -> Decimal | None:
        with self._lock:
            quote = self._quotes.get(sembol)
            if quote is not None:
                return quote.orta
            return self._last_price.get(sembol)

    def bid(self, sembol: str) -> Decimal | None:
        """Satış tarafı değerleme ve elle kapatma için en iyi alış."""
        with self._lock:
            quote = self._quotes.get(sembol)
            if quote is not None:
                return quote.alis
            return self._last_price.get(sembol)

    def usdttry(self) -> Decimal | None:
        with self._lock:
            return self._last_price.get(USDTTRY)

    def last_event(self, sembol: str) -> datetime | None:
        with self._lock:
            return self._last_event.get(sembol)

    def spread_median(self, sembol: str) -> Decimal | None:
        with self._lock:
            samples = list(self._spreads.get(sembol, ()))
        if len(samples) < SPREAD_MIN_SAMPLES:
            return None
        return Decimal(str(statistics.median(samples)))

    def spread_samples(self, sembol: str) -> int:
        with self._lock:
            return len(self._spreads.get(sembol, ()))

    def btc_move_pct(self) -> Decimal | None:
        with self._lock:
            window = list(self._minutes.get(BTC, ()))
        if len(window) < 30:
            return None
        reference = window[0].open
        if reference <= ZERO:
            return None
        high = max(item.high for item in window)
        low = min(item.low for item in window)
        up = (high / reference - 1) * ONE_HUNDRED
        down = (1 - low / reference) * ONE_HUNDRED
        return max(up, down)

    def atr(self, sembol: str, periyot: str) -> tuple[Decimal | None, Decimal | None]:
        """(güncel ATR %, son 30 günün medyan ATR %'si) — disk deposundan."""
        from albsat.features.indicators import atr_percent

        frame = self.store.read(sembol, periyot)
        if frame.empty:
            return None, None
        frame = closed_only(frame)
        last_open = int(frame["open_time"].max())
        key = (sembol, periyot)
        cached = self._atr_cache.get(key)
        if cached is not None and cached[0] == last_open:
            return cached[1], cached[2]
        bars = int(ATR_MEDIAN_DAYS * 86_400_000 / interval_ms(periyot))
        tail = frame.tail(bars + ATR_PERIOD * 3).reset_index(drop=True)
        series = atr_percent(tail, ATR_PERIOD).to_numpy(dtype=float)
        valid = series[np.isfinite(series)]
        current = float(series[-1]) if len(series) and np.isfinite(series[-1]) else None
        median = float(np.median(valid[-bars:])) if len(valid) >= ATR_PERIOD else None
        result = (
            None if current is None else Decimal(f"{current:.6f}"),
            None if median is None else Decimal(f"{median:.6f}"),
        )
        self._atr_cache[key] = (last_open, result[0], result[1])
        return result

    def market_state(
        self, sembol: str, periyot: str, rules: SymbolRules | None
    ) -> MarketState:
        atr_now, atr_median = self.atr(sembol, periyot)
        quote = self.quote(sembol)
        notes: list[str] = []
        samples = self.spread_samples(sembol)
        if samples < SPREAD_MIN_SAMPLES:
            notes.append(
                f"Spread'in normal değeri için {SPREAD_MIN_SAMPLES} gözlem gerekiyor, "
                f"şu an {samples}. Uygulama açıldıktan yaklaşık 5 dakika sonra ölçülür."
            )
        with self._lock:
            volume = self._volume.get(sembol)
        return MarketState(
            sembol=sembol,
            son_veri_utc=self.last_event(sembol),
            son_fiyat=self.last_price(sembol),
            atr_yuzde=atr_now,
            atr_medyan_yuzde=atr_median,
            spread_yuzde=None if quote is None else spread_pct(quote.alis, quote.satis),
            spread_medyan_yuzde=self.spread_median(sembol),
            hacim_24s_usdt=volume,
            btc_60dk_degisim_yuzde=self.btc_move_pct(),
            kurallar=rules,
            notlar=tuple(notes),
        )


__all__ = ["BTC", "USDTTRY", "LiveMarket", "Quote"]
