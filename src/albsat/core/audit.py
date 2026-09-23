"""Denetim kaydı (SPEC.md §4.7: "Tüm mod değişiklikleri, ayar değişiklikleri
ve emirler denetim kaydına yazılır").

Kayıt yalnızca eklenir; hiçbir kod satır güncellemez ya da silmez. Her satır
kimin (``kaynak``: arayüz, Telegram, canlı döngü, risk motoru) neyi
değiştirdiğini ve ayrıntısını taşır. Ayrıntı JSON'dur ama **sır içermez**:
API anahtarı, Telegram jetonu ya da imza hiçbir zaman buraya yazılmaz.
Yazılmadığını ``test_denetim_kaydi_sir_tasimaz`` doğrular.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from albsat.core import db
from albsat.core.clock import iso, istanbul_text, utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS denetim_kaydi (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    zaman_utc   TEXT NOT NULL,
    tur         TEXT NOT NULL,
    kaynak      TEXT NOT NULL,
    ozet        TEXT NOT NULL,
    ayrinti     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS denetim_kaydi_zaman ON denetim_kaydi (zaman_utc);
"""

#: Kaynaklar.
SOURCE_UI = "arayuz"
SOURCE_TELEGRAM = "telegram"
SOURCE_LOOP = "canli_dongu"
SOURCE_RISK = "risk_motoru"
SOURCE_SYSTEM = "sistem"

SOURCE_LABELS_TR = {
    SOURCE_UI: "arayüz",
    SOURCE_TELEGRAM: "Telegram",
    SOURCE_LOOP: "canlı döngü",
    SOURCE_RISK: "risk motoru",
    SOURCE_SYSTEM: "sistem",
}

#: Ayrıntıya asla yazılmaması gereken anahtar adı parçaları.
_FORBIDDEN_KEY_PARTS = ("token", "jeton", "secret", "gizli", "private", "signature", "imza")


@dataclass(frozen=True)
class AuditEntry:
    id: int
    zaman_utc: str
    tur: str
    kaynak: str
    ozet: str
    ayrinti: dict[str, Any]

    @property
    def zaman_istanbul(self) -> str:
        return istanbul_text(self.zaman_utc)

    @property
    def kaynak_tr(self) -> str:
        return SOURCE_LABELS_TR.get(self.kaynak, self.kaynak)


def _clean(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Sır taşıyabilecek anahtarları kayda hiç sokmaz."""
    if not detail:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in detail.items():
        if any(part in key.lower() for part in _FORBIDDEN_KEY_PARTS):
            continue
        cleaned[key] = value if isinstance(value, (int, float, bool, type(None))) else str(value)
    return cleaned


class AuditLog:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        db.ensure_schema(self.path, _SCHEMA)

    @classmethod
    def in_directory(cls, root: Path | str) -> AuditLog:
        return cls(db.path_in(root))

    def write(
        self,
        tur: str,
        ozet: str,
        *,
        kaynak: str = SOURCE_SYSTEM,
        ayrinti: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> int:
        with db.session(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO denetim_kaydi (zaman_utc, tur, kaynak, ozet, ayrinti) "
                "VALUES (?,?,?,?,?)",
                (
                    iso(now or utc_now()),
                    tur,
                    kaynak,
                    ozet,
                    json.dumps(_clean(ayrinti), ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid or 0)

    def recent(self, limit: int = 100) -> tuple[AuditEntry, ...]:
        with db.session(self.path) as connection:
            rows = connection.execute(
                "SELECT * FROM denetim_kaydi ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return tuple(
            AuditEntry(
                id=int(row["id"]),
                zaman_utc=row["zaman_utc"],
                tur=row["tur"],
                kaynak=row["kaynak"],
                ozet=row["ozet"],
                ayrinti=json.loads(row["ayrinti"] or "{}"),
            )
            for row in rows
        )


__all__ = [
    "SOURCE_LABELS_TR",
    "SOURCE_LOOP",
    "SOURCE_RISK",
    "SOURCE_SYSTEM",
    "SOURCE_TELEGRAM",
    "SOURCE_UI",
    "AuditEntry",
    "AuditLog",
]
