"""Zaman: içeride UTC, sınırlar İstanbul saatine göre (SPEC.md §2).

Risk limitleri "günlük", "haftalık" ve "aylık" diye tanımlanır. Bu günün
sınırı kullanıcının günüdür: Berk için yeni gün İstanbul'da gece yarısı
başlar, UTC'de değil. UTC'ye göre kesilen bir günlük zarar sınırı, gece
03:00'te (İstanbul) sıfırlanırdı ve "ertesi gün" beklenenden üç saat önce
gelirdi.

Türkiye 2016'dan beri yaz saati uygulamıyor; saat farkı sabit +3. Sabit fark
kullanmak, macOS'taki saat dilimi veritabanının sürümüne bağımlı olmamak
demektir.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

#: İstanbul saati (UTC+3, yaz saati yok).
ISTANBUL = timezone(timedelta(hours=3))


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(moment: datetime) -> datetime:
    """Saat dilimi olmayan zamanı UTC sayar; olanı UTC'ye çevirir."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def parse_utc(text: str | None) -> datetime | None:
    """ISO metnini UTC ``datetime``'a çevirir; okunamazsa ``None``."""
    if not text:
        return None
    try:
        return as_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def iso(moment: datetime) -> str:
    """Saniyeye yuvarlanmış UTC ISO metni (kayıtlarda tek biçim)."""
    return as_utc(moment).replace(microsecond=0).isoformat()


def from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def to_ms(moment: datetime) -> int:
    return int(as_utc(moment).timestamp() * 1000)


def istanbul_text(moment: datetime | str | None) -> str:
    """Arayüzde gösterilecek İstanbul saati (gg.aa.yyyy ss:dd)."""
    if moment is None:
        return ""
    if isinstance(moment, str):
        parsed = parse_utc(moment)
        if parsed is None:
            return moment
        moment = parsed
    return as_utc(moment).astimezone(ISTANBUL).strftime("%d.%m.%Y %H:%M")


def day_start(moment: datetime) -> datetime:
    """``moment``'in içinde bulunduğu İstanbul gününün başlangıcı (UTC)."""
    local = as_utc(moment).astimezone(ISTANBUL)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def week_start(moment: datetime) -> datetime:
    """İstanbul'a göre haftanın başlangıcı (pazartesi 00:00), UTC."""
    local = as_utc(moment).astimezone(ISTANBUL)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start.astimezone(UTC)


def month_start(moment: datetime) -> datetime:
    """İstanbul'a göre ayın ilk günü 00:00, UTC."""
    local = as_utc(moment).astimezone(ISTANBUL)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def next_day_start(moment: datetime) -> datetime:
    return day_start(day_start(moment) + timedelta(hours=27))


__all__ = [
    "ISTANBUL",
    "as_utc",
    "day_start",
    "from_ms",
    "iso",
    "istanbul_text",
    "month_start",
    "next_day_start",
    "parse_utc",
    "to_ms",
    "utc_now",
    "week_start",
]
