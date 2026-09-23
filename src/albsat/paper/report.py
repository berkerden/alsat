"""Kâğıt işlem sonuçlarının özeti ve CSV dökümü (SPEC.md §4.8).

Kural işlemleri ile elle girilen işlemler **ayrı** özetlenir: kuralların
beklentiyle kıyası elle emirlerle karışırsa hangisinin ne getirdiği
görünmez.

Özet metrikleri: işlem sayısı, isabet oranı, net kâr/zarar, işlem başına
ortalama net, kâr faktörü, en büyük düşüş, ödenen toplam komisyon ve
komisyonun brüt kâra oranı, ortalama tutma süresi. Kural işlemlerinde
kartın beklediği net ile gerçekleşenin farkı (sapma) da verilir.

CSV her işlemi **iki satır** yazar (alış ve satış); muhasebe ve vergi
kaydında her biri ayrı bir alım-satım işlemidir. Sütunlar: tarih, çift,
yön, fiyat, miktar, komisyon, USDT ve TRY karşılığı (SPEC §4.8). TRY
karşılığı işlem anındaki USDTTRY kurundan hesaplanır; kur bilinmiyorsa boş
bırakılır, uydurulmaz.
"""

from __future__ import annotations

import csv
import io
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from albsat.core.clock import istanbul_text, parse_utc
from albsat.core.money import ZERO
from albsat.paper.fills import EXIT_LABELS_TR
from albsat.paper.ledger import STATUS_CLOSED, PaperOrder
from albsat.risk.engine import SOURCE_MANUAL, SOURCE_RULE

SOURCE_LABELS_TR = {SOURCE_RULE: "kural", SOURCE_MANUAL: "elle"}

#: Canlıya geçiş kapısı (SPEC §4.7): kâğıtta en az bu kadar gün ve işlem.
GATE_MIN_DAYS = 7
GATE_MIN_TRADES = 30


@dataclass(frozen=True)
class Summary:
    kaynak: str
    islem: int
    kazanan: int
    isabet_orani: float | None
    net_usdt: Decimal
    ortalama_net_yuzde: float | None
    kar_faktoru: float | None
    en_buyuk_dusus_usdt: Decimal
    toplam_komisyon_usdt: Decimal
    brut_kar_usdt: Decimal
    komisyon_brut_kar_orani: float | None
    ortalama_tutma_dk: float | None
    beklenen_ortalama_yuzde: float | None
    sapma_yuzde: float | None
    cikis_sebepleri: dict[str, int]


def _commission_usdt(order: PaperOrder) -> Decimal:
    """Alış komisyonu coin olarak alınır; giriş fiyatından USDT'ye çevrilir."""
    entry = order.dec("komisyon_coin") * order.dec("giris")
    return entry + order.dec("cikis_komisyon_usdt")


def _gross(order: PaperOrder) -> Decimal:
    """Komisyon öncesi sonuç: net + ödenen komisyon."""
    return order.dec("net_usdt") + _commission_usdt(order)


def summarize(orders: Iterable[PaperOrder], kaynak: str) -> Summary:
    trades = [
        item for item in orders if item.durum == STATUS_CLOSED and item.kaynak == kaynak
    ]
    trades.sort(key=lambda item: (item.cikis_utc or "", item.id))
    nets = [item.dec("net_usdt") for item in trades]
    pcts = [float(item.net_yuzde or 0.0) for item in trades]
    wins = sum(1 for value in nets if value > ZERO)
    profit = sum((value for value in nets if value > ZERO), ZERO)
    loss = -sum((value for value in nets if value < ZERO), ZERO)
    equity = peak = drawdown = ZERO
    for value in nets:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    commission = sum((_commission_usdt(item) for item in trades), ZERO)
    gross_profit = sum((max(_gross(item), ZERO) for item in trades), ZERO)
    holds: list[float] = []
    for item in trades:
        start, end = parse_utc(item.dolum_utc), parse_utc(item.cikis_utc)
        if start is not None and end is not None:
            holds.append((end - start).total_seconds() / 60)
    expected = [
        item.beklenen_ortalama_yuzde for item in trades
        if item.beklenen_ortalama_yuzde is not None
    ]
    mean_pct = statistics.fmean(pcts) if pcts else None
    mean_expected = statistics.fmean(expected) if expected else None
    reasons: dict[str, int] = {}
    for item in trades:
        label = EXIT_LABELS_TR.get(item.cikis_sebebi or "", item.cikis_sebebi or "?")
        reasons[label] = reasons.get(label, 0) + 1
    return Summary(
        kaynak=kaynak,
        islem=len(trades),
        kazanan=wins,
        isabet_orani=wins / len(trades) if trades else None,
        net_usdt=sum(nets, ZERO),
        ortalama_net_yuzde=mean_pct,
        kar_faktoru=float(profit / loss) if loss > ZERO else None,
        en_buyuk_dusus_usdt=drawdown,
        toplam_komisyon_usdt=commission,
        brut_kar_usdt=gross_profit,
        komisyon_brut_kar_orani=float(commission / gross_profit) if gross_profit > ZERO
        else None,
        ortalama_tutma_dk=statistics.fmean(holds) if holds else None,
        beklenen_ortalama_yuzde=mean_expected,
        sapma_yuzde=None if mean_pct is None or mean_expected is None
        else mean_pct - mean_expected,
        cikis_sebepleri=reasons,
    )


@dataclass(frozen=True)
class LiveGate:
    """Canlıya geçiş kapısının bir kural için durumu (bilgi amaçlı, Faz 6'da zorunlu)."""

    kural_kimligi: str
    kural_etiketi: str
    gun: float
    islem: int
    gecti: bool
    aciklama: str


def live_gates(orders: Sequence[PaperOrder], now: datetime) -> list[LiveGate]:
    by_rule: dict[str, list[PaperOrder]] = {}
    for item in orders:
        if item.kaynak == SOURCE_RULE and item.kural_kimligi and item.durum == STATUS_CLOSED:
            by_rule.setdefault(item.kural_kimligi, []).append(item)
    result: list[LiveGate] = []
    for rule_id, trades in sorted(by_rule.items()):
        first = min(parse_utc(item.olusturma_utc) or now for item in trades)
        days = (now - first) / timedelta(days=1)
        count = len(trades)
        passed = days >= GATE_MIN_DAYS and count >= GATE_MIN_TRADES
        text = (
            f"{days:.1f} gün, {count} işlem (gereken en az {GATE_MIN_DAYS} gün ve "
            f"{GATE_MIN_TRADES} işlem). "
            + ("Süre ve sayı koşulu sağlandı; beklentiyle uyum ayrıca sınanır."
               if passed else "Henüz yetersiz.")
        )
        result.append(LiveGate(rule_id, trades[-1].kural_etiketi or rule_id, days, count,
                                passed, text))
    return result


CSV_COLUMNS = (
    "tarih_istanbul",
    "tarih_utc",
    "cift",
    "yon",
    "fiyat",
    "miktar",
    "komisyon",
    "komisyon_varligi",
    "komisyon_usdt",
    "usdt_karsiligi",
    "try_karsiligi",
    "usdttry_kuru",
    "islem_net_usdt",
    "kaynak",
    "kural",
    "cikis_sebebi",
    "emir_kimligi",
    "not",
)


def _try(value: Decimal, rate: str | None) -> str:
    if not rate:
        return ""
    return str((value * Decimal(rate)).quantize(Decimal("0.01")))


def csv_rows(orders: Iterable[PaperOrder]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in sorted(orders, key=lambda order: order.id):
        if not item.dolum_utc:
            continue
        base = item.sembol.removesuffix("USDT")
        source = SOURCE_LABELS_TR.get(item.kaynak, item.kaynak)
        buy_value = item.dec("tutar_usdt")
        rows.append({
            "tarih_istanbul": istanbul_text(item.dolum_utc),
            "tarih_utc": item.dolum_utc or "",
            "cift": item.sembol,
            "yon": "ALIŞ",
            "fiyat": item.giris,
            "miktar": item.miktar,
            "komisyon": item.komisyon_coin or "",
            "komisyon_varligi": base,
            "komisyon_usdt": str(item.dec("komisyon_coin") * item.dec("giris")),
            "usdt_karsiligi": str(buy_value),
            "try_karsiligi": _try(buy_value, item.usdttry),
            "usdttry_kuru": item.usdttry or "",
            "islem_net_usdt": "",
            "kaynak": source,
            "kural": item.kural_etiketi or "",
            "cikis_sebebi": "",
            "emir_kimligi": item.istemci_kimligi,
            "not": "kâğıt işlem (simülasyon)",
        })
        if item.durum != STATUS_CLOSED:
            continue
        sell_value = item.dec("gelir_usdt") + item.dec("cikis_komisyon_usdt")
        rows.append({
            "tarih_istanbul": istanbul_text(item.cikis_utc),
            "tarih_utc": item.cikis_utc or "",
            "cift": item.sembol,
            "yon": "SATIŞ",
            "fiyat": item.cikis_fiyati or "",
            "miktar": item.satilacak or "",
            "komisyon": item.cikis_komisyon_usdt or "",
            "komisyon_varligi": "USDT",
            "komisyon_usdt": item.cikis_komisyon_usdt or "",
            "usdt_karsiligi": str(sell_value),
            "try_karsiligi": _try(sell_value, item.usdttry),
            "usdttry_kuru": item.usdttry or "",
            "islem_net_usdt": item.net_usdt or "",
            "kaynak": source,
            "kural": item.kural_etiketi or "",
            "cikis_sebebi": EXIT_LABELS_TR.get(item.cikis_sebebi or "", ""),
            "emir_kimligi": item.istemci_kimligi,
            "not": "kâğıt işlem (simülasyon)",
        })
    return rows


def to_csv(orders: Iterable[PaperOrder]) -> str:
    """Excel'in Türkçe ayarlarında da doğru açılsın diye ``;`` ayraç ve BOM."""
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), delimiter=";")
    writer.writeheader()
    writer.writerows(csv_rows(orders))
    return buffer.getvalue()


__all__ = [
    "CSV_COLUMNS",
    "GATE_MIN_DAYS",
    "GATE_MIN_TRADES",
    "LiveGate",
    "Summary",
    "csv_rows",
    "live_gates",
    "summarize",
    "to_csv",
]
