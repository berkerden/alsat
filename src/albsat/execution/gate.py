"""Canlıya geçiş kapısı (SPEC.md §4.7; Faz 6 kabul kriteri).

SPEC: *"Bir strateji paper modda en az [7 gün / 30 işlem] çalışıp beklentiyle
uyumlu sonuç vermeden Tam Otomatik açılamaz."*

Kapı **kural başınadır**. Bir kural ancak şu koşulların hepsi sağlanınca
geçer:

1. Kural şu anki kural deposunda **kabul edilmiş** (teşhis turu değil).
   Kabul edilmemiş aday hiçbir koşulda otomatik işlenmez.
2. Kâğıtta ilk kural işleminden bu yana en az ``GATE_MIN_DAYS`` gün geçmiş.
3. Kâğıtta en az ``GATE_MIN_TRADES`` kapanmış **kural** işlemi var. Elle
   işlemler sayılmaz (Faz 4 kararı: elle emir kapıya girmez).
4. Beklentiyle uyum: performans sınaması yapılabilmiş (yeterli örneklem) ve
   gerçekleşen ortalama net, beklenenin anlamlı ölçüde altında değil
   (Faz 4'ün ``performance_check``'i, tek yönlü).
5. Kâğıttaki ortalama net sıfırın üstünde. SPEC'te açıkça yazmıyor; beklentiyle
   "uyumlu" ama zarar eden bir kuralı gerçek paraya taşımamak için eklendi.
6. Kural performans koruması tarafından durdurulmamış.

Bir coin için Tam Otomatik, o coinde kapıyı geçen en az bir kural varsa
açılabilir; Tam Otomatik'te yalnızca kapıyı geçen kuralların sinyali emre
dönüşür. Kapıyı elle aşan bir yol (SPEC'teki "açık uyarı ekranı ve ek onay")
Faz 6'da yazılmadı: Berk'in 23 Eylül 2026 kararıyla Tam Otomatik kapıda
kilitli kalıyor.

Bu modül ağa çıkmaz ve hiçbir şey değiştirmez; yalnızca hesaplar.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from albsat.core.clock import parse_utc
from albsat.paper.ledger import STATUS_CLOSED, PaperOrder
from albsat.paper.report import GATE_MIN_DAYS, GATE_MIN_TRADES
from albsat.risk.engine import SOURCE_RULE, PerformanceCheck
from albsat.strategy.rules import RuleSet


@dataclass(frozen=True)
class Condition:
    ad: str
    etiket: str
    gecti: bool
    aciklama: str


@dataclass(frozen=True)
class RuleGate:
    kural_kimligi: str
    kural_etiketi: str
    sembol: str
    periyot: str
    gun: float
    islem: int
    ortalama_net_yuzde: float | None
    beklenen_yuzde: float | None
    kosullar: tuple[Condition, ...]

    @property
    def gecti(self) -> bool:
        return all(item.gecti for item in self.kosullar)

    @property
    def eksikler(self) -> tuple[str, ...]:
        return tuple(item.aciklama for item in self.kosullar if not item.gecti)


def _parts(rule_id: str) -> tuple[str, str]:
    parts = rule_id.split("|")
    return (parts[0], parts[1]) if len(parts) >= 2 else ("", "")


def rule_gates(
    *,
    ruleset: RuleSet | None,
    paper_orders: Iterable[PaperOrder],
    checks: Sequence[PerformanceCheck],
    disabled: Sequence[str],
    now: datetime,
) -> list[RuleGate]:
    """Kabul edilmiş her kural ve kâğıtta işlemi olan her kural için kapı durumu."""
    trades: dict[str, list[PaperOrder]] = {}
    for item in paper_orders:
        if item.kaynak == SOURCE_RULE and item.kural_kimligi and item.durum == STATUS_CLOSED:
            trades.setdefault(item.kural_kimligi, []).append(item)
    accepted = {} if ruleset is None or ruleset.kosu.teshis_turu else {
        rule.kimlik: rule for rule in ruleset.kurallar}
    by_check = {item.kural_kimligi: item for item in checks}
    disabled_set = set(disabled)
    result: list[RuleGate] = []
    for rule_id in sorted(set(accepted) | set(trades)):
        rule = accepted.get(rule_id)
        orders = trades.get(rule_id, [])
        first = min((parse_utc(item.olusturma_utc) or now for item in orders), default=None)
        days = 0.0 if first is None else (now - first) / timedelta(days=1)
        count = len(orders)
        check = by_check.get(rule_id)
        mean = check.gerceklesen_yuzde if check is not None else None
        label = rule.etiket if rule is not None else (
            orders[-1].kural_etiketi if orders and orders[-1].kural_etiketi else rule_id)
        symbol, period = (rule.sembol, rule.periyot) if rule is not None else _parts(rule_id)
        tested = check is not None and check.gerceklesen_yuzde is not None
        conditions = (
            Condition("kabul", "Kabul edilmiş kural", rule is not None,
                      "Kural deposunda kabul edilmiş." if rule is not None else
                      "Kural şu anki kural deposunda kabul edilmiş değil; otomatik işlenmez."),
            Condition("gun", f"Kâğıtta en az {GATE_MIN_DAYS} gün", days >= GATE_MIN_DAYS,
                      f"Kâğıtta {days:.1f} gün (gereken {GATE_MIN_DAYS})."),
            Condition("islem", f"Kâğıtta en az {GATE_MIN_TRADES} işlem",
                      count >= GATE_MIN_TRADES,
                      f"Kâğıtta {count} kapanmış kural işlemi (gereken {GATE_MIN_TRADES}; "
                      "elle işlemler sayılmaz)."),
            Condition("uyum", "Beklentiyle uyum", tested and not check.bozuk,  # type: ignore[union-attr]
                      check.aciklama if check is not None else
                      "Performans sınaması için kâğıt sonucu yok."),
            Condition("pozitif", "Kâğıtta net pozitif", mean is not None and mean > 0,
                      "Kâğıtta ortalama net henüz hesaplanmadı." if mean is None else
                      f"Kâğıtta işlem başına ortalama net %{mean:+.4f}."),
            Condition("durdurulmadi", "Durdurulmamış", rule_id not in disabled_set,
                      "Kural performans korumasıyla durdurulmuş." if rule_id in disabled_set
                      else "Kural etkin."),
        )
        result.append(RuleGate(
            kural_kimligi=rule_id, kural_etiketi=label, sembol=symbol, periyot=period,
            gun=days, islem=count, ortalama_net_yuzde=mean,
            beklenen_yuzde=check.beklenen_yuzde if check is not None else None,
            kosullar=conditions,
        ))
    return result


def passed_for(symbol: str, gates: Sequence[RuleGate]) -> list[RuleGate]:
    return [item for item in gates if item.sembol == symbol and item.gecti]


def symbol_summary(symbol: str, gates: Sequence[RuleGate]) -> str:
    """Bir coin için kapının tek cümlelik özeti (Tam Otomatik açılabilir mi?)."""
    own = [item for item in gates if item.sembol == symbol]
    passed = [item for item in own if item.gecti]
    if passed:
        return (f"{symbol}: {len(passed)} kural canlıya geçiş kapısını geçti "
                f"({', '.join(item.kural_etiketi for item in passed)}).")
    if not own:
        return (f"{symbol}: kabul edilmiş kural yok; Tam Otomatik açılamaz. Kural deposunda "
                "bu coin için kabul edilmiş bir kural çıkması ve kâğıtta en az "
                f"{GATE_MIN_DAYS} gün / {GATE_MIN_TRADES} işlem beklentiyle uyumlu "
                "çalışması gerekir.")
    return (f"{symbol}: hiçbir kural kapıyı geçmedi; Tam Otomatik açılamaz. "
            + " ".join(f"{item.kural_etiketi}: {item.eksikler[0]}" for item in own
                       if item.eksikler))


__all__ = [
    "GATE_MIN_DAYS",
    "GATE_MIN_TRADES",
    "Condition",
    "RuleGate",
    "passed_for",
    "rule_gates",
    "symbol_summary",
]
