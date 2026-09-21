"""Olay çalışması: "bu koşul oluştuktan sonra fiyat ne yaptı?" (SPEC.md §4.2).

Her mum için, o mumda bir sinyal verilmiş gibi davranıp sonraki ``N`` mumda
ne olacağını simüle eder. Örüntü arayıcı bu tabloyu bir kez hesaplar, sonra
her örüntü için yalnızca satır süzer; yüzlerce örüntü bu yüzden saniyeler
içinde taranabiliyor.

**Zaman çizelgesi — nedenselliğin tamamı burada:**

    mum i kapanır ──► sinyal hesaplanır (yalnızca i ve öncesi)
    mum i+1 açılır ──► POZİSYON BURADA AÇILIR
    mum i+1 … i+N  ──► hedef mi stop mu önce geldi, ölçülür

Sinyalin hesaplandığı mumun **kapanışından** işleme girilmez; o fiyat mum
kapandığı anda artık yoktur. Giriş bir sonraki mumun açılışıdır. Bu tek
satırlık fark, geçmişe dönük testlerde en sık yapılan ve sonucu en çok
şişiren hatadır.

**Aynı mumda hem hedefe hem stopa değilirse stop kabul edilir.** Mum verisi
(açılış/yüksek/düşük/kapanış) hangisinin önce geldiğini söylemez. İyimser
varsayım burada sonuçları sistematik olarak güzelleştirir; temkinli varsayım
en kötü ihtimali gösterir. Gerçek sıralama ancak tik verisiyle bilinir.

**Decimal/float ayrımı:** Fiyat, miktar ve bakiye borsaya giderken her zaman
``Decimal``'dir (SPEC.md §3). Buradaki hesap borsaya gitmez; on binlerce mum
üzerinde istatistik üretir ve ``float`` ile yapılır. Komisyon oranları yine
``albsat.core.costs`` içindeki ``Decimal`` motordan alınır, yalnızca
vektörleştirme anında ``float``'a çevrilir. ``tests/test_eventstudy.py``
iki yolun aynı sonucu verdiğini doğrular.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from albsat.core.costs import FeePayment, RoundTrip, TargetThreshold
from albsat.core.fees import Liquidity
from albsat.features.indicators import atr_percent

#: ``exit_reason`` kodları.
EXIT_TARGET = 0
EXIT_STOP = 1
EXIT_TIMEOUT = 2

EXIT_LABELS_TR = {
    EXIT_TARGET: "hedef",
    EXIT_STOP: "stop",
    EXIT_TIMEOUT: "süre doldu",
}


@dataclass(frozen=True)
class OutcomeConfig:
    """Bir olay çalışmasının kuralları.

    ``horizon``: pozisyonun en fazla kaç mum tutulacağı. Faz 1'in bulgusu
    gereği varsayılanlar 2–4 mumdur: kârlılığı hangi grafiğe bakıldığı değil,
    pozisyonda ne kadar kalındığı belirliyor.

    ``target_atr`` / ``stop_atr``: hedef ve stop, o mumdaki ATR'nin katı
    olarak konur. Sabit yüzde kullanmak sakin ve oynak dönemleri aynı kabul
    ederdi.
    """

    horizon: int
    target_atr: float = 1.0
    stop_atr: float = 1.0
    atr_period: int = 14
    entry_slippage_pct: float = 0.0
    exit_slippage_pct: float = 0.02
    entry_liquidity: Liquidity = Liquidity.TAKER

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise ValueError("horizon en az 1 olmalı")
        if self.target_atr <= 0 or self.stop_atr <= 0:
            raise ValueError("hedef ve stop ATR katsayıları pozitif olmalı")

    @property
    def label_tr(self) -> str:
        return (
            f"{self.horizon} mum, hedef {self.target_atr:g}×ATR, "
            f"stop {self.stop_atr:g}×ATR"
        )


@dataclass(frozen=True)
class OutcomeTable:
    """Her mum için simüle edilmiş sonuç. Satır sayısı mum sayısına eşittir."""

    config: OutcomeConfig
    eligible: np.ndarray
    entry_price: np.ndarray
    target_price: np.ndarray
    stop_price: np.ndarray
    exit_price: np.ndarray
    exit_reason: np.ndarray
    bars_held: np.ndarray
    target_pct: np.ndarray
    stop_pct: np.ndarray
    mfe_pct: np.ndarray
    mae_pct: np.ndarray
    net_pct: np.ndarray
    forward_pct: np.ndarray
    rejected_by_threshold: int = 0

    def __len__(self) -> int:
        return int(len(self.eligible))

    @property
    def eligible_count(self) -> int:
        return int(self.eligible.sum())


def _fee_rates(trip: RoundTrip) -> tuple[float, float, bool]:
    return float(trip.entry.rate), float(trip.exit.rate), trip.payment is FeePayment.IN_BNB


def net_margin_pct_array(
    trip: RoundTrip, entry: np.ndarray, exit_price: np.ndarray
) -> np.ndarray:
    """``RoundTrip.net_margin_pct``'in vektörleştirilmiş karşılığı.

    Formüller ``albsat.core.costs`` ile birebir aynıdır; testler iki yolun
    aynı sayıyı verdiğini doğrular.
    """
    fee_in, fee_out, in_bnb = _fee_rates(trip)
    with np.errstate(invalid="ignore", divide="ignore"):
        if in_bnb:
            gross = exit_price - entry
            fees = fee_in * entry + fee_out * exit_price
            result = (gross - fees) / entry * 100.0
        else:
            proceeds = exit_price * (1.0 - fee_in) * (1.0 - fee_out)
            result = (proceeds - entry) / entry * 100.0
    return np.asarray(result, dtype=float)


def build_outcomes(
    frame: pd.DataFrame,
    *,
    config: OutcomeConfig,
    trip_to_target: RoundTrip,
    trip_to_stop: RoundTrip,
    threshold: TargetThreshold | None = None,
    ready: pd.Series | np.ndarray | None = None,
) -> OutcomeTable:
    """Her mum için "burada girseydik ne olurdu" tablosunu üretir.

    ``trip_to_target``: hedefe limit emirle çıkılan tur (çıkış maker).
    ``trip_to_stop``: stopla veya süre dolduğunda piyasa emriyle çıkılan tur
    (çıkış taker). İkisinin ayrı olmasının sebebi FAZ0-MIMARI.md Risk #4:
    tek bir "gidiş-dönüş komisyonu" sabiti yanlıştır.

    ``threshold`` verilirse SPEC.md §4.3'ün otomatik elemesi uygulanır:
    hedefi maliyet eşiğinin altında kalan mumlar uygun sayılmaz.
    """
    count = int(len(frame))
    horizon = config.horizon
    nan = float("nan")

    if count <= horizon:
        empty = np.full(count, nan)
        return OutcomeTable(
            config=config,
            eligible=np.zeros(count, dtype=bool),
            entry_price=empty.copy(),
            target_price=empty.copy(),
            stop_price=empty.copy(),
            exit_price=empty.copy(),
            exit_reason=np.full(count, EXIT_TIMEOUT, dtype=np.int8),
            bars_held=np.zeros(count, dtype=np.int16),
            target_pct=empty.copy(),
            stop_pct=empty.copy(),
            mfe_pct=empty.copy(),
            mae_pct=empty.copy(),
            net_pct=empty.copy(),
            forward_pct=empty.copy(),
        )

    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)

    volatility = atr_percent(frame, config.atr_period).to_numpy(dtype=float)

    # Giriş bir SONRAKİ mumun açılışıdır. Son mumun girişi yoktur.
    entry = np.full(count, nan)
    entry[:-1] = open_[1:] * (1.0 + config.entry_slippage_pct / 100.0)

    target_pct = volatility * config.target_atr
    stop_pct = volatility * config.stop_atr
    target_price = entry * (1.0 + target_pct / 100.0)
    stop_price = entry * (1.0 - stop_pct / 100.0)

    index = np.arange(count)
    eligible = (
        (index + horizon < count)
        & np.isfinite(entry)
        & np.isfinite(volatility)
        & (volatility > 0.0)
    )
    if ready is not None:
        eligible &= np.asarray(ready, dtype=bool)

    rejected = 0
    if threshold is not None:
        # SPEC.md §4.3: hedefi maliyeti karşılamayan sinyal otomatik elenir.
        minimum = float(threshold.minimum_target_pct)
        passes = target_pct >= minimum
        rejected = int((eligible & ~passes).sum())
        eligible &= passes

    active = eligible.copy()
    exit_reason = np.full(count, EXIT_TIMEOUT, dtype=np.int8)
    exit_price = np.full(count, nan)
    bars_held = np.zeros(count, dtype=np.int16)
    best = np.full(count, -np.inf)
    worst = np.full(count, np.inf)

    def shifted(values: np.ndarray, step: int) -> np.ndarray:
        out = np.full(count, nan)
        out[: count - step] = values[step:]
        return out

    for step in range(1, horizon + 1):
        high_step, low_step = shifted(high, step), shifted(low, step)
        with np.errstate(invalid="ignore"):
            best = np.where(active, np.fmax(best, high_step), best)
            worst = np.where(active, np.fmin(worst, low_step), worst)

            touched_stop = active & (low_step <= stop_price)
            touched_target = active & (high_step >= target_price)

        # Aynı mumda ikisi de görülürse stop kabul edilir (temkinli varsayım).
        stop_now = touched_stop
        target_now = touched_target & ~touched_stop

        exit_price = np.where(stop_now, stop_price, exit_price)
        exit_price = np.where(target_now, target_price, exit_price)
        exit_reason = np.where(stop_now, EXIT_STOP, exit_reason).astype(np.int8)
        exit_reason = np.where(target_now, EXIT_TARGET, exit_reason).astype(np.int8)
        bars_held = np.where(stop_now | target_now, step, bars_held).astype(np.int16)
        active &= ~(stop_now | target_now)

    # Hedefe de stopa da değmeden süre dolduysa son mumun kapanışından çıkılır.
    timed_out = eligible & active
    close_at_horizon = shifted(close, horizon)
    exit_price = np.where(timed_out, close_at_horizon, exit_price)
    bars_held = np.where(timed_out, horizon, bars_held).astype(np.int16)

    # Stop ve süre dolumu piyasa emridir: kayma çıkışta aleyhimize işler.
    market_exit = eligible & (exit_reason != EXIT_TARGET)
    exit_fill = np.where(
        market_exit, exit_price * (1.0 - config.exit_slippage_pct / 100.0), exit_price
    )

    net_target = net_margin_pct_array(trip_to_target, entry, exit_fill)
    net_stop = net_margin_pct_array(trip_to_stop, entry, exit_fill)
    net_pct = np.where(exit_reason == EXIT_TARGET, net_target, net_stop)
    net_pct = np.where(eligible, net_pct, nan)

    with np.errstate(invalid="ignore"):
        mfe_pct = np.where(eligible, (best - entry) / entry * 100.0, nan)
        mae_pct = np.where(eligible, (worst - entry) / entry * 100.0, nan)
        forward_pct = np.where(eligible, (close_at_horizon - entry) / entry * 100.0, nan)

    return OutcomeTable(
        config=config,
        eligible=eligible,
        entry_price=np.where(eligible, entry, nan),
        target_price=np.where(eligible, target_price, nan),
        stop_price=np.where(eligible, stop_price, nan),
        exit_price=np.where(eligible, exit_fill, nan),
        exit_reason=exit_reason,
        bars_held=bars_held,
        target_pct=np.where(eligible, target_pct, nan),
        stop_pct=np.where(eligible, stop_pct, nan),
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        net_pct=net_pct,
        forward_pct=forward_pct,
        rejected_by_threshold=rejected,
    )


@dataclass(frozen=True)
class EventSummary:
    """Bir örüntünün olay istatistikleri (SPEC.md §4.2 "her örüntü için ölçülecekler")."""

    events: int
    independent_events: int
    hit_rate: float
    up_rate: float
    net_mean_pct: float
    net_median_pct: float
    net_p25_pct: float
    net_p75_pct: float
    mfe_median_pct: float
    mae_median_pct: float
    bars_held_mean: float
    target_share: float
    stop_share: float
    timeout_share: float

    @property
    def expectancy_tr(self) -> str:
        return f"İşlem başına net %{self.net_mean_pct:+.4f} (n={self.events})"


def summarize(outcomes: OutcomeTable, mask: np.ndarray) -> EventSummary:
    """Seçilen mumlar için olay istatistiklerini üretir.

    ``independent_events`` üst üste binmeyen olay sayısıdır: pencere
    ``horizon`` mum sürdüğü için ardışık sinyaller aynı fiyat hareketini
    tekrar tekrar sayar. İstatistiksel testler bu sayıyı kullanır, çünkü
    üst üste binen olaylar bağımsız kanıt değildir.
    """
    selected = mask & outcomes.eligible
    count = int(selected.sum())
    if count == 0:
        return EventSummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    net = outcomes.net_pct[selected]
    reason = outcomes.exit_reason[selected]
    return EventSummary(
        events=count,
        independent_events=int(len(independent_indices(selected, outcomes.config.horizon))),
        hit_rate=float((reason == EXIT_TARGET).mean()),
        up_rate=float((outcomes.forward_pct[selected] > 0).mean()),
        net_mean_pct=float(np.mean(net)),
        net_median_pct=float(np.median(net)),
        net_p25_pct=float(np.percentile(net, 25)),
        net_p75_pct=float(np.percentile(net, 75)),
        mfe_median_pct=float(np.median(outcomes.mfe_pct[selected])),
        mae_median_pct=float(np.median(outcomes.mae_pct[selected])),
        bars_held_mean=float(np.mean(outcomes.bars_held[selected])),
        target_share=float((reason == EXIT_TARGET).mean()),
        stop_share=float((reason == EXIT_STOP).mean()),
        timeout_share=float((reason == EXIT_TIMEOUT).mean()),
    )


def independent_indices(mask: np.ndarray, horizon: int) -> np.ndarray:
    """Üst üste binmeyen olayların indeksleri.

    Bir olay ``horizon`` mum sürer. İlk olay alınır, o pencere bitmeden
    başlayan olaylar atlanır, sonraki alınır. Aynı hareketi birden çok kez
    saymamak için.
    """
    positions = np.flatnonzero(mask)
    if positions.size == 0:
        return positions
    kept = [int(positions[0])]
    for position in positions[1:]:
        if int(position) - kept[-1] > horizon:
            kept.append(int(position))
    return np.array(kept, dtype=int)


def forward_window_profile(
    frame: pd.DataFrame, *, horizons: tuple[int, ...] = (1, 3, 5, 10, 20)
) -> pd.DataFrame:
    """SPEC.md §4.2'nin istediği "sonraki 1/3/5/10/20 mumda getiri dağılımı".

    Örüntüden bağımsız taban çizgisi: piyasanın kendisi bu pencerelerde ne
    yapıyor? Bir örüntünün sayıları buna göre okunur.
    """
    open_ = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    count = len(frame)
    rows = []
    for horizon in horizons:
        if count <= horizon + 1:
            continue
        entry = open_[1 : count - horizon + 1]
        exit_price = close[horizon : count]
        returns = (exit_price - entry) / entry * 100.0
        rows.append(
            {
                "mum": horizon,
                "n": int(len(returns)),
                "ortalama%": float(np.mean(returns)),
                "medyan%": float(np.median(returns)),
                "p10%": float(np.percentile(returns, 10)),
                "p90%": float(np.percentile(returns, 90)),
                "yukari_oran": float((returns > 0).mean()),
            }
        )
    return pd.DataFrame(rows)


def to_decimal_pct(value: float) -> Decimal:
    """İstatistik sonucunu rapora yazarken ``Decimal``'e çevirir."""
    return Decimal(f"{float(value):.6f}")
