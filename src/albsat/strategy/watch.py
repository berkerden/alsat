"""İzleme paneli — B ekinin "sık al-sat yerine takip et" tarafı.

Faz 2'nin ölçümünden sonra bu panelin işi net: kullanıcının izlediği sembolde
**şu an ne olduğunu** göstermek, ve gördüğü hareketin maliyet eşiğine göre
büyüklüğünü söylemek. Emir göndermez, sinyal üretmez, tahmin yapmaz.

Panelin cevapladığı sorular:

* Son kapanmış mum ne zamandı, veri taze mi?
* Fiyat nerede: son kapanış, gün içi aralık, 7 ve 30 günlük değişim.
* Tipik mum hareketi (ATR%) maliyet eşiğinin kaç katı? Yani bugünkü oynaklık
  bir al-satı anlamlı kılacak büyüklükte mi?
* Kullanıcının koyduğu fiyat sınırlarına uzaklık ne kadar?

Fiyat sınırı (``seviyeler``) bir alarm değildir: uygulama Faz 3'te bildirim
göndermez, ekranda gösterir. Telegram bildirimi Faz 4'ün işi.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np

from albsat.core.costs import minimum_meaningful_target
from albsat.core.money import ONE_HUNDRED, ZERO, Number, to_decimal
from albsat.data.klines import candles_per_day, closed_only, interval_ms, to_utc
from albsat.data.store import KlineStore
from albsat.features.indicators import atr_percent
from albsat.strategy.rules import CostAssumptions
from albsat.strategy.signals import STALE_AFTER_BARS, cost_trips


@dataclass(frozen=True)
class LevelDistance:
    """Kullanıcının koyduğu bir fiyat sınırına uzaklık."""

    etiket: str
    fiyat: Decimal
    uzaklik_yuzde: Decimal
    #: Uzaklık, maliyet eşiğinin kaç katı? 1'in altı "gidilse bile maliyeti
    #: karşılamaz" demek.
    esik_kati: Decimal


@dataclass(frozen=True)
class WatchRow:
    """Tek bir sembol + periyot için izleme satırı."""

    sembol: str
    periyot: str
    son_kapanis_utc: str
    son_fiyat: Decimal
    mum_sayisi: int
    bayat: bool
    gecikme_mum: float

    gun_ici_dusuk: Decimal
    gun_ici_yuksek: Decimal
    degisim_1g_yuzde: float
    degisim_7g_yuzde: float
    degisim_30g_yuzde: float

    atr_yuzde: Decimal
    atr_yuzde_medyan: Decimal
    maliyet_esigi_yuzde: Decimal
    seviyeler: tuple[LevelDistance, ...] = ()

    @property
    def oynaklik_orani(self) -> Decimal:
        """Bugünkü ATR%, maliyet eşiğinin kaç katı?"""
        if self.maliyet_esigi_yuzde <= ZERO:
            return ZERO
        return self.atr_yuzde / self.maliyet_esigi_yuzde

    @property
    def oynaklik_notu_tr(self) -> str:
        ratio = self.oynaklik_orani
        if ratio < 1:
            return (
                f"Tipik mum hareketi maliyetin {ratio:.2f} katı — bu periyotta "
                "al-sat matematiksel olarak zararına."
            )
        if ratio < 2:
            return f"Tipik mum hareketi maliyetin {ratio:.2f} katı — marj dar."
        return f"Tipik mum hareketi maliyetin {ratio:.2f} katı."

    @property
    def gunluk_atr_karsilastirmasi(self) -> str:
        """Bugünkü oynaklık, dönem medyanına göre nerede?"""
        if self.atr_yuzde_medyan <= ZERO:
            return ""
        ratio = self.atr_yuzde / self.atr_yuzde_medyan
        if ratio >= Decimal("1.5"):
            return "Oynaklık olağandan yüksek."
        if ratio <= Decimal("0.67"):
            return "Oynaklık olağandan düşük."
        return "Oynaklık olağan aralıkta."


@dataclass(frozen=True)
class WatchBoard:
    """İzleme panelinin tamamı."""

    satirlar: tuple[WatchRow, ...]
    olusturma_utc: str
    not_tr: str = (
        "Bu panel yalnızca gösterir: emir göndermez, sinyal üretmez ve "
        "bildirim yollamaz. Sayılar diskteki kapanmış mumlardan okunur."
    )


def _change_pct(close: np.ndarray, bars: int) -> float:
    if bars <= 0 or close.size <= bars:
        return 0.0
    past, now = float(close[-1 - bars]), float(close[-1])
    if past == 0.0:
        return 0.0
    return (now - past) / past * 100.0


def build_board(
    *,
    semboller: Sequence[tuple[str, str]],
    veri_dizini: Path | str,
    maliyet: CostAssumptions,
    seviyeler: dict[str, dict[str, Number]] | None = None,
    now: datetime | None = None,
) -> WatchBoard:
    """İzleme panelini kurar.

    ``semboller`` ``(sembol, periyot)`` çiftleridir. ``seviyeler`` isteğe
    bağlı: ``{"BTCUSDT": {"alt sınır": "100000", "üst sınır": "130000"}}``.
    """
    now = now or datetime.now(UTC)
    store = KlineStore(veri_dizini)
    _, trip_to_stop = cost_trips(maliyet)
    threshold = minimum_meaningful_target(
        trip_to_stop,
        spread_pct=maliyet.spread_yuzde,
        slippage_pct=maliyet.kayma_yuzde,
        safety_pct=maliyet.guvenlik_payi_yuzde,
    ).minimum_target_pct

    rows: list[WatchRow] = []
    for sembol, periyot in semboller:
        frame = store.read(sembol, periyot)
        if frame.empty:
            continue
        frame = closed_only(frame).reset_index(drop=True)
        if frame.empty:
            continue

        close = frame["close"].to_numpy(dtype=float)
        per_day = max(1, int(round(candles_per_day(periyot))))
        step_ms = interval_ms(periyot)
        last_open = int(frame["open_time"].iloc[-1])
        last_close_time = to_utc(last_open + step_ms)
        delay = (now - last_close_time).total_seconds()

        atr_series = atr_percent(frame, 14).to_numpy(dtype=float)
        finite = atr_series[np.isfinite(atr_series)]
        current_atr = float(atr_series[-1]) if np.isfinite(atr_series[-1]) else 0.0
        median_atr = float(np.median(finite)) if finite.size else 0.0

        day_slice = frame.tail(per_day)
        last_price = to_decimal(f"{float(close[-1]):.10f}")

        levels: list[LevelDistance] = []
        for etiket, fiyat in (seviyeler or {}).get(sembol, {}).items():
            level = to_decimal(fiyat)
            if last_price <= ZERO:
                continue
            distance = (level - last_price) / last_price * ONE_HUNDRED
            magnitude = abs(distance)
            levels.append(
                LevelDistance(
                    etiket=etiket,
                    fiyat=level,
                    uzaklik_yuzde=distance,
                    esik_kati=(magnitude / threshold) if threshold > ZERO else ZERO,
                )
            )

        rows.append(
            WatchRow(
                sembol=sembol,
                periyot=periyot,
                son_kapanis_utc=last_close_time.isoformat(),
                son_fiyat=last_price,
                mum_sayisi=int(len(frame)),
                bayat=delay > STALE_AFTER_BARS * step_ms / 1000.0,
                gecikme_mum=delay / (step_ms / 1000.0) if step_ms else 0.0,
                gun_ici_dusuk=to_decimal(f"{float(day_slice['low'].min()):.10f}"),
                gun_ici_yuksek=to_decimal(f"{float(day_slice['high'].max()):.10f}"),
                degisim_1g_yuzde=_change_pct(close, per_day),
                degisim_7g_yuzde=_change_pct(close, per_day * 7),
                degisim_30g_yuzde=_change_pct(close, per_day * 30),
                atr_yuzde=to_decimal(f"{current_atr:.6f}"),
                atr_yuzde_medyan=to_decimal(f"{median_atr:.6f}"),
                maliyet_esigi_yuzde=threshold,
                seviyeler=tuple(levels),
            )
        )

    return WatchBoard(
        satirlar=tuple(rows),
        olusturma_utc=now.replace(microsecond=0).isoformat(),
    )


__all__ = ["LevelDistance", "WatchBoard", "WatchRow", "build_board"]
