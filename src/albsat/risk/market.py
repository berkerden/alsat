"""Piyasa koşulu filtreleri (SPEC.md §4.6).

"Aşırı volatilite, spread'in normalin k katına çıkması, düşük likidite,
BTC'de sert hareket, bayat veri → yeni işlem açılmaz." Bu modül o beş
koşulu ve coin uygunluğunu ölçer. Karar vermez; her koşul için bir
``Gate`` sonucu üretir ve kararı risk motoruna bırakır.

Ölçülemeyen bir koşul **geçmiş sayılmaz**. Örneğin spread hiç ölçülemediyse
(fiyat defteri isteği başarısız) kapı "ölçülemedi" der ve işlem açılmaz.
Bilinmeyen bir koşulu iyi saymak, filtrenin tam gerektiği anda (bağlantı
bozukken, piyasa çalkantılıyken) devre dışı kalması demektir.

Coinin "Monitoring/Seed" etiketi Spot API'de yayımlanmıyor; bu yüzden uygunluk
kontrolü borsanın yayımladığı sembol durumuna (``TRADING`` dışında her şey
engel) ve hacme dayanır. Etiket kontrolü uydurulmadı.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from albsat.core.clock import as_utc, istanbul_text
from albsat.core.filters import SymbolRules
from albsat.core.money import ONE_HUNDRED, ZERO
from albsat.risk.limits import RiskLimits


@dataclass(frozen=True)
class Gate:
    """Tek bir risk kapısının sonucu. Arayüzde satır satır gösterilir."""

    ad: str
    etiket: str
    gecti: bool
    aciklama: str
    #: Ölçülemediyse ``True``: kapı kapalı sayılır ama sebep farklıdır.
    olculemedi: bool = False


@dataclass(frozen=True)
class MarketState:
    """Bir coin için karar anındaki piyasa ölçümleri.

    Alanlar ölçülemediyse ``None``'dır; filtreler ``None``'ı geçmiş saymaz.
    """

    sembol: str
    #: Son kapanmış 1 dakikalık mumun kapanış zamanı.
    son_veri_utc: datetime | None = None
    son_fiyat: Decimal | None = None
    #: Güncel ATR yüzdesi (işlem periyodunda).
    atr_yuzde: Decimal | None = None
    #: Son dönemin medyan ATR yüzdesi (aynı periyot).
    atr_medyan_yuzde: Decimal | None = None
    #: Fiyat defterinin en iyi alış/satış farkı, orta fiyata oranla yüzde.
    spread_yuzde: Decimal | None = None
    #: Gözlenen spread'lerin medyanı; henüz yeterli gözlem yoksa ``None``.
    spread_medyan_yuzde: Decimal | None = None
    #: Son 24 saatin işlem hacmi (USDT).
    hacim_24s_usdt: Decimal | None = None
    #: BTC'nin son 60 dakikadaki en büyük mutlak değişimi (yüzde).
    btc_60dk_degisim_yuzde: Decimal | None = None
    kurallar: SymbolRules | None = None
    notlar: tuple[str, ...] = field(default_factory=tuple)


def _fmt(value: Decimal, places: int = 4) -> str:
    return f"{value:.{places}f}"


def stale_gate(state: MarketState, limits: RiskLimits, now: datetime) -> Gate:
    label = "Veri tazeliği"
    if state.son_veri_utc is None:
        return Gate("bayat_veri", label, False, "Canlı fiyat alınamadı.", olculemedi=True)
    # Saatler arasındaki milisaniyelik fark "-0 saniye" yazdırmasın.
    age = max(0.0, (as_utc(now) - as_utc(state.son_veri_utc)).total_seconds())
    if age > limits.bayat_veri_azami_saniye:
        return Gate(
            "bayat_veri",
            label,
            False,
            f"Son fiyat {age:.0f} saniye önce ({istanbul_text(state.son_veri_utc)}); "
            f"sınır {limits.bayat_veri_azami_saniye} saniye.",
        )
    return Gate("bayat_veri", label, True, f"Son fiyat {age:.0f} saniye önce.")


def volatility_gate(state: MarketState, limits: RiskLimits) -> Gate:
    label = "Oynaklık"
    if state.atr_yuzde is None or state.atr_medyan_yuzde is None or state.atr_medyan_yuzde <= 0:
        return Gate("volatilite", label, False, "ATR ölçülemedi.", olculemedi=True)
    ratio = state.atr_yuzde / state.atr_medyan_yuzde
    text = (
        f"ATR %{_fmt(state.atr_yuzde)}, dönem medyanı %{_fmt(state.atr_medyan_yuzde)} "
        f"({ratio:.2f} kat; sınır {limits.volatilite_kati} kat)."
    )
    return Gate("volatilite", label, ratio <= limits.volatilite_kati, text)


#: BTC'de spread çoğu zaman bir fiyat adımıdır (≈ %0,00002); dört basamak
#: onu "%0,0000" gösterirdi.
SPREAD_PLACES = 6


def spread_gate(state: MarketState, limits: RiskLimits) -> Gate:
    label = "Spread"
    if state.spread_yuzde is None:
        return Gate("spread", label, False, "Fiyat defteri okunamadı.", olculemedi=True)
    spread = state.spread_yuzde
    if spread > limits.spread_azami_yuzde:
        return Gate(
            "spread",
            label,
            False,
            f"Spread %{_fmt(spread, SPREAD_PLACES)}, üst sınır %{limits.spread_azami_yuzde}.",
        )
    median = state.spread_medyan_yuzde
    if median is not None and median > ZERO and spread > median * limits.spread_kati:
        return Gate(
            "spread",
            label,
            False,
            f"Spread %{_fmt(spread, SPREAD_PLACES)}, normalin (%{_fmt(median, SPREAD_PLACES)}) "
            f"{spread / median:.1f} katı; sınır {limits.spread_kati} kat.",
        )
    normal = f", normal %{_fmt(median, SPREAD_PLACES)}" if median is not None else ""
    return Gate("spread", label, True, f"Spread %{_fmt(spread, SPREAD_PLACES)}{normal}.")


def liquidity_gate(state: MarketState, limits: RiskLimits) -> Gate:
    label = "Likidite"
    if state.hacim_24s_usdt is None:
        return Gate("likidite", label, False, "24 saatlik hacim ölçülemedi.", olculemedi=True)
    volume = state.hacim_24s_usdt
    text = f"24 saatlik hacim {volume:,.0f} USDT (asgari {limits.min_24s_hacim_usdt:,.0f})."
    return Gate("likidite", label, volume >= limits.min_24s_hacim_usdt, text)


def btc_gate(state: MarketState, limits: RiskLimits) -> Gate:
    label = "BTC hareketi"
    move = state.btc_60dk_degisim_yuzde
    if move is None:
        return Gate("btc_hareketi", label, False, "BTC hareketi ölçülemedi.", olculemedi=True)
    text = (
        f"BTC son 60 dakikada en fazla %{_fmt(move, 2)} oynadı "
        f"(sınır %{limits.btc_sert_hareket_yuzde})."
    )
    return Gate("btc_hareketi", label, move <= limits.btc_sert_hareket_yuzde, text)


def eligibility_gate(state: MarketState) -> Gate:
    label = "Coin uygunluğu"
    rules = state.kurallar
    if rules is None:
        return Gate(
            "coin_uygunlugu",
            label,
            False,
            "Borsa filtreleri (tickSize, stepSize, asgari tutar) elde yok; fiyat ve "
            "miktar borsanın kabul edeceği biçime yuvarlanamaz.",
            olculemedi=True,
        )
    if not rules.tradable:
        return Gate(
            "coin_uygunlugu",
            label,
            False,
            f"Borsa bu sembolde yeni emir kabul etmiyor (durum: {rules.status}).",
        )
    return Gate("coin_uygunlugu", label, True, f"{rules.symbol} işleme açık (TRADING).")


def market_gates(state: MarketState, limits: RiskLimits, now: datetime) -> tuple[Gate, ...]:
    """Beş piyasa filtresi ve coin uygunluğu, sabit sırayla."""
    return (
        stale_gate(state, limits, now),
        eligibility_gate(state),
        volatility_gate(state, limits),
        spread_gate(state, limits),
        liquidity_gate(state, limits),
        btc_gate(state, limits),
    )


def spread_pct(bid: Decimal, ask: Decimal) -> Decimal | None:
    """En iyi alış/satış farkının orta fiyata oranı (yüzde)."""
    if bid <= ZERO or ask <= ZERO or ask < bid:
        return None
    middle = (bid + ask) / 2
    return (ask - bid) / middle * ONE_HUNDRED


__all__ = [
    "Gate",
    "MarketState",
    "btc_gate",
    "eligibility_gate",
    "liquidity_gate",
    "market_gates",
    "spread_gate",
    "spread_pct",
    "stale_gate",
    "volatility_gate",
]
