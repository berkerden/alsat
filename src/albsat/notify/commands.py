"""Telegram komutlarının yanıtları (SPEC.md §8.10: ``/durum``, ``/durdur``, ``/onayla``).

Komutlar Telegram'a bağlı değildir: metin alır, metin döner. Böylece
testte ve ileride başka bir kanalda da aynı yanıt üretilir.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from albsat.core.audit import SOURCE_TELEGRAM
from albsat.core.clock import istanbul_text, utc_now
from albsat.modes.state import MODE_LABELS_TR
from albsat.paper.engine import PaperEngine
from albsat.paper.ledger import STATUS_OPEN, STATUS_PENDING
from albsat.paper.report import summarize
from albsat.risk import engine as risk

HELP_TEXT = (
    "Komutlar:\n"
    "/durum — modlar, kâğıt hesap, açık pozisyonlar, bugünkü sonuç\n"
    "/durdur — ACİL DURDUR: kâğıt işlemi, Demo'yu ve canlı modları kapatır, bekleyen "
    "girişleri iptal eder; açık pozisyonların stop ve hedefi yerinde kalır\n"
    "/durdur kapat — aynı şey, ayrıca açık pozisyonları kapatır (kâğıtta piyasa "
    "fiyatından, Demo'da ve canlıda korumalı satışla)\n"
    "/onayla — onay bekleyen canlı önerileri listeler\n"
    "/onayla <kimlik> — o öneriyi GERÇEK PARAYLA canlı hesaba gönderir (Yarı Otomatik)\n"
    "/reddet <kimlik> — öneriyi reddeder\n"
    "/yardim — bu liste"
)


def _usdt(value: Decimal | None) -> str:
    return "?" if value is None else f"{value.quantize(Decimal('0.01'))}"


def status_text(engine: PaperEngine, *, marks: dict[str, Decimal],
                connection: dict[str, Any] | None, now: datetime) -> str:
    lines = [f"📊 DURUM — {istanbul_text(now)}"]
    if connection is not None:
        if connection.get("akis_bagli"):
            lines.append("Bağlantı: canlı akış bağlı")
        elif connection.get("yedek_yoklama"):
            lines.append("Bağlantı: akış kopuk, yedek yoklama çalışıyor")
        else:
            lines.append("Bağlantı: KOPUK")
    lines.append(" · ".join(
        f"{item.sembol}: {MODE_LABELS_TR.get(item.mod, item.mod)}" for item in engine.modes.all()
    ))
    view = engine.account(marks)
    equity = (
        f"özsermaye {_usdt(view.ozsermaye_usdt)} USDT (%{view.getiri_yuzde:+.2f})"
        if view.ozsermaye_usdt is not None and view.getiri_yuzde is not None
        else "özsermaye hesaplanamadı (fiyat yok)"
    )
    lines.append(f"Kâğıt hesap: nakit {_usdt(view.nakit_usdt)} USDT, {equity}")
    metrics = risk.metrics(engine.snapshot(now), engine.limits, now)
    lines.append(
        f"Bugün: net {_usdt(metrics.gunluk_net_usdt)} USDT, günlük zarar sınırına kalan "
        f"{_usdt(metrics.gunluk_kalan_usdt)} USDT, art arda kayıp {metrics.art_arda_kayip}"
    )
    for order in engine.ledger.active():
        if order.durum == STATUS_OPEN:
            lines.append(
                f"Açık: {order.sembol} {order.satilacak} @ {order.giris} "
                f"(hedef {order.hedef}, stop {order.stop})"
            )
        elif order.durum == STATUS_PENDING:
            lines.append(f"Bekleyen giriş: {order.sembol} {order.miktar} @ {order.giris}")
    period_id, _, _ = engine.period()
    orders = engine.ledger.in_period(period_id)
    for kaynak, label in ((risk.SOURCE_RULE, "Kural"), (risk.SOURCE_MANUAL, "Elle")):
        summary = summarize(orders, kaynak)
        if summary.islem:
            lines.append(
                f"{label} işlemleri: {summary.islem} işlem, net {_usdt(summary.net_usdt)} USDT"
            )
    disabled = engine.disabled_rules()
    if disabled:
        lines.append(f"Durdurulan kurallar: {', '.join(disabled)}")
    return "\n".join(lines)


def demo_text(demo: Any, *, marks: dict[str, Decimal]) -> list[str]:
    """Demo Mode satırları (yürütücü yoksa boş)."""
    if demo is None:
        return []
    reason = demo.not_ready_reason()
    lines = ["Demo Mode: hazır" if reason is None else f"Demo Mode: kullanılamıyor ({reason})"]
    if demo.trader is None:
        return lines
    view = demo.account(marks)
    lines.append(f"Demo bot bütçesi: nakit {_usdt(view.nakit_usdt)} USDT"
                 + (f", özsermaye {_usdt(view.ozsermaye_usdt)} USDT"
                    if view.ozsermaye_usdt is not None else ""))
    for position in demo.ledger.active():
        lines.append(f"Demo: {position.sembol} #{position.id} {position.durum_tr} "
                     f"(giriş {position.giris}, hedef {position.hedef}, stop {position.stop})")
    return lines


def live_text(live: Any, *, marks: dict[str, Decimal]) -> list[str]:
    """Canlı hesap satırları (yürütücü yoksa ya da anahtar kurulu değilse kısa)."""
    if live is None or live.trader is None:
        return []
    reason = live.not_ready_reason()
    lines = ["Canlı hesap (GERÇEK PARA): hazır" if reason is None
             else f"Canlı hesap: kullanılamıyor ({reason})"]
    view = live.account(marks)
    lines.append(f"Canlı bot bütçesi: nakit {_usdt(view.nakit_usdt)} USDT"
                 + (f", özsermaye {_usdt(view.ozsermaye_usdt)} USDT"
                    if view.ozsermaye_usdt is not None else ""))
    for position in live.ledger.active():
        lines.append(f"CANLI: {position.sembol} #{position.id} {position.durum_tr} "
                     f"(giriş {position.giris}, hedef {position.hedef}, stop {position.stop})")
    waiting = live.waiting()
    if waiting:
        lines.append("Onay bekleyen canlı öneri: " + ", ".join(
            f"{item.kimlik} ({item.sembol})" for item in waiting))
    return lines


def _proposals_text(live: Any) -> str:
    if live is None or live.trader is None:
        return "Canlı işlem anahtarı kurulu değil; onay bekleyen emir yok."
    waiting = live.waiting()
    if not waiting:
        return ("Onay bekleyen canlı öneri yok. Yarı Otomatik moddaki bir coinde kural "
                "sinyali gelince öneri buraya düşer.")
    lines = ["Onay bekleyen canlı öneriler (GERÇEK PARA):"]
    for item in waiting:
        lines.append(f"{item.kimlik}: {item.sembol} {item.periyot} {item.kural_etiketi}, giriş "
                     f"{item.giris}, hedef {item.hedef}, stop {item.stop}; "
                     f"{istanbul_text(item.bitis_utc)}'a kadar")
    lines.append("Göndermek için: /onayla <kimlik>")
    return "\n".join(lines)


def build_handler(
    engine: PaperEngine,
    *,
    marks: Callable[[], dict[str, Decimal]],
    connection: Callable[[], dict[str, Any] | None],
    clock: Callable[[], datetime] = utc_now,
    demo: Any = None,
    demo_marks: Callable[[], dict[str, Decimal]] | None = None,
    live: Any = None,
) -> Callable[[str, str], str]:
    def handle(command: str, args: str) -> str:
        now = clock()
        if command in ("/start", "/yardim", "/yardım", "/help"):
            return HELP_TEXT
        if command == "/durum":
            text = status_text(engine, marks=marks(), connection=connection(), now=now)
            extra = [*demo_text(demo, marks=(demo_marks or marks)()),
                     *live_text(live, marks=marks())]
            return "\n".join([text, *extra]) if extra else text
        if command in ("/durdur", "/durdur_kapat"):
            close = command == "/durdur_kapat" or args.lower().startswith("kapat")
            events = engine.kill_switch(
                close_positions=close, marks=marks(), source=SOURCE_TELEGRAM, now=now
            )
            live_closing = 0
            if live is not None:
                live_closing = live.kill_switch(close_positions=close, source=SOURCE_TELEGRAM)
            demo_closing = 0
            if demo is not None:
                demo_closing = demo.kill_switch(close_positions=close, source=SOURCE_TELEGRAM)
            parts = [
                "⛔ Acil durdurma çalıştı. Kâğıt işlem, Demo ve canlı modlar kapandı, bütün "
                "coinler 'Sadece Öneri' modunda.",
                f"İptal edilen bekleyen kâğıt emir: {len(events.iptal)}.",
            ]
            if live is not None and live.trader is not None:
                parts.append("Canlı hesaptaki bekleyen girişler borsada iptal ediliyor.")
            if demo is not None and demo.trader is not None:
                parts.append("Demo'daki bekleyen girişler borsada iptal ediliyor.")
            if close:
                parts.append(f"Piyasa fiyatından kapatılan kâğıt pozisyon: "
                             f"{len(events.kapanan)}.")
                if live_closing:
                    parts.append(f"Korumalı satışla kapatılan CANLI pozisyon: {live_closing}.")
                if demo_closing:
                    parts.append(f"Korumalı satışla kapatılan Demo pozisyonu: {demo_closing}.")
            else:
                parts.append("Açık pozisyonların stop ve hedefi yerinde. Kapatmak için: "
                             "/durdur kapat")
            parts.append("Yeniden başlatmak yalnızca arayüzden yapılır.")
            return "\n".join(parts)
        if command == "/onayla":
            wanted = args.strip().split()[0] if args.strip() else ""
            if not wanted or live is None or live.trader is None:
                return _proposals_text(live)
            try:
                result = live.approve(wanted, source=SOURCE_TELEGRAM)
            except ValueError as error:
                return f"Gönderilmedi: {error}"
            if result.pozisyon is None:
                return f"Canlı emir açılmadı: {result.mesaj}"
            return (f"✅ Öneri {wanted}: {result.mesaj} (#{result.pozisyon.id}, "
                    f"{result.pozisyon.sembol}). Dolum ve koruma bildirimleri ayrıca gelir.")
        if command == "/reddet":
            wanted = args.strip().split()[0] if args.strip() else ""
            if not wanted or live is None:
                return _proposals_text(live)
            try:
                item = live.reject(wanted, source=SOURCE_TELEGRAM)
            except ValueError as error:
                return f"Reddedilemedi: {error}"
            return f"Öneri {item.kimlik} ({item.sembol}) reddedildi; emir gönderilmedi."
        return f"Bilinmeyen komut: {command}\n\n{HELP_TEXT}"

    return handle


__all__ = ["HELP_TEXT", "build_handler", "demo_text", "live_text", "status_text"]
