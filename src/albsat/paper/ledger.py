"""Kâğıt işlem kayıtları (SQLite).

Bir kâğıt emrinin bütün hayatı **tek satırda** tutulur: verildi → doldu
(pozisyon açık) → kapandı, ya da verildi → iptal edildi. Kısmi dolum
modellenmediği için (100 USDT'lik emirler BTCUSDT ve SOLUSDT defterinde tek
seferde dolar) ayrı bir dolum tablosuna gerek yok; tek satır, "önerilen vs
gerçekleşen" kıyasını ve CSV dışa aktarımını da basitleştiriyor.

Bakiyeler ayrı bir tabloda **tutulmaz**, satırlardan hesaplanır. İkinci bir
bakiye kaydı, er ya da geç emirlerle ayrışır ve hangisinin doğru olduğu
bilinemez. Tek doğruluk kaynağı emir satırlarıdır.

Hesap dönemleri: kullanıcı kâğıt hesabını sıfırlarsa yeni bir dönem başlar.
Eski işlemler silinmez (geçmiş kayıt kalır), yalnızca güncel bakiyeye ve
risk hesabına katılmaz.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from albsat.core import db
from albsat.core.clock import iso, parse_utc, utc_now

STATUS_PENDING = "bekliyor"
STATUS_OPEN = "acik"
STATUS_CLOSED = "kapandi"
STATUS_CANCELLED = "iptal"

STATUS_LABELS_TR = {
    STATUS_PENDING: "giriş bekliyor",
    STATUS_OPEN: "pozisyon açık",
    STATUS_CLOSED: "kapandı",
    STATUS_CANCELLED: "iptal edildi",
}

CLIENT_ID_PREFIX = "albsat-kagit-"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kagit_hesap_donemleri (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    baslangic_utc   TEXT NOT NULL,
    baslangic_usdt  TEXT NOT NULL,
    bitis_utc       TEXT
);

CREATE TABLE IF NOT EXISTS kagit_emirleri (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    donem_id                  INTEGER NOT NULL REFERENCES kagit_hesap_donemleri(id),
    istemci_kimligi           TEXT NOT NULL UNIQUE,
    olusturma_utc             TEXT NOT NULL,
    aktif_ms                  INTEGER NOT NULL,
    sembol                    TEXT NOT NULL,
    periyot                   TEXT NOT NULL,
    kaynak                    TEXT NOT NULL,
    kural_kimligi             TEXT,
    kural_etiketi             TEXT,
    sinyal_mumu_utc           TEXT,
    giris                     TEXT NOT NULL,
    hedef                     TEXT NOT NULL,
    stop                      TEXT NOT NULL,
    miktar                    TEXT NOT NULL,
    tutar_usdt                TEXT NOT NULL,
    stop_zarari_usdt          TEXT NOT NULL,
    gecerlilik_bitis_ms       INTEGER NOT NULL,
    azami_tutma_ms            INTEGER NOT NULL,
    beklenen_hedef_net_yuzde  REAL,
    beklenen_ortalama_yuzde   REAL,
    yeniden_fiyatlama         INTEGER NOT NULL DEFAULT 0,
    durum                     TEXT NOT NULL,
    iptal_sebebi              TEXT,
    dolum_utc                 TEXT,
    dolum_ms                  INTEGER,
    komisyon_coin             TEXT,
    alinan                    TEXT,
    onceki_toz                TEXT,
    satilacak                 TEXT,
    toz                       TEXT,
    cikis_utc                 TEXT,
    cikis_fiyati              TEXT,
    cikis_sebebi              TEXT,
    gelir_usdt                TEXT,
    cikis_komisyon_usdt       TEXT,
    net_usdt                  TEXT,
    net_yuzde                 REAL,
    usdttry                   TEXT,
    notlar                    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS kagit_emirleri_durum ON kagit_emirleri (durum);

CREATE TABLE IF NOT EXISTS kagit_durum (
    anahtar   TEXT PRIMARY KEY,
    deger     TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class PaperOrder:
    """``kagit_emirleri`` tablosunun bir satırı."""

    id: int
    donem_id: int
    istemci_kimligi: str
    olusturma_utc: str
    aktif_ms: int
    sembol: str
    periyot: str
    kaynak: str
    kural_kimligi: str | None
    kural_etiketi: str | None
    sinyal_mumu_utc: str | None
    giris: str
    hedef: str
    stop: str
    miktar: str
    tutar_usdt: str
    stop_zarari_usdt: str
    gecerlilik_bitis_ms: int
    azami_tutma_ms: int
    beklenen_hedef_net_yuzde: float | None
    beklenen_ortalama_yuzde: float | None
    yeniden_fiyatlama: int
    durum: str
    iptal_sebebi: str | None
    dolum_utc: str | None
    dolum_ms: int | None
    komisyon_coin: str | None
    alinan: str | None
    onceki_toz: str | None
    satilacak: str | None
    toz: str | None
    cikis_utc: str | None
    cikis_fiyati: str | None
    cikis_sebebi: str | None
    gelir_usdt: str | None
    cikis_komisyon_usdt: str | None
    net_usdt: str | None
    net_yuzde: float | None
    usdttry: str | None
    notlar: str

    # --- Decimal erişimi ------------------------------------------------

    def dec(self, name: str) -> Decimal:
        value = getattr(self, name)
        return Decimal(value) if value not in (None, "") else Decimal("0")

    @property
    def not_listesi(self) -> list[str]:
        try:
            items = json.loads(self.notlar or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item) for item in items] if isinstance(items, list) else []

    @property
    def durum_tr(self) -> str:
        return STATUS_LABELS_TR.get(self.durum, self.durum)

    @property
    def kapanis(self) -> datetime | None:
        return parse_utc(self.cikis_utc)


_COLUMNS = tuple(item.name for item in fields(PaperOrder))


def new_client_id(kaynak: str) -> str:
    """Borsanın ``newClientOrderId`` kuralına uyan (≤36 karakter) kimlik.

    Faz 5'te gerçek emirler de aynı önek düzeniyle adlandırılacak; bot yalnızca
    kendi önekini taşıyan emirleri yönetecek (SPEC §4.5).
    """
    stamp = format(int(time.time() * 1000), "x")
    return f"{CLIENT_ID_PREFIX}{kaynak[:5]}-{stamp}{secrets.token_hex(2)}"[:36]


class PaperLedger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        db.ensure_schema(self.path, _SCHEMA)

    @classmethod
    def in_directory(cls, root: Path | str) -> PaperLedger:
        return cls(db.path_in(root))

    # --- hesap dönemi ----------------------------------------------------

    def current_period(self, *, default_start_usdt: Decimal) -> tuple[int, Decimal, str]:
        """Açık hesap dönemi; yoksa ``default_start_usdt`` ile açar."""
        with db.session(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM kagit_hesap_donemleri WHERE bitis_utc IS NULL "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                now = iso(utc_now())
                cursor = connection.execute(
                    "INSERT INTO kagit_hesap_donemleri (baslangic_utc, baslangic_usdt) "
                    "VALUES (?, ?)",
                    (now, str(default_start_usdt)),
                )
                return int(cursor.lastrowid or 0), default_start_usdt, now
            return int(row["id"]), Decimal(row["baslangic_usdt"]), row["baslangic_utc"]

    def reset_period(self, *, start_usdt: Decimal, now: datetime) -> int:
        """Yeni dönem açar. Açık emir/pozisyon varken çağrılmamalı (çağıran denetler)."""
        with db.session(self.path) as connection:
            connection.execute(
                "UPDATE kagit_hesap_donemleri SET bitis_utc = ? WHERE bitis_utc IS NULL",
                (iso(now),),
            )
            cursor = connection.execute(
                "INSERT INTO kagit_hesap_donemleri (baslangic_utc, baslangic_usdt) VALUES (?, ?)",
                (iso(now), str(start_usdt)),
            )
            return int(cursor.lastrowid or 0)

    # --- emirler -----------------------------------------------------------

    def insert(self, values: dict[str, Any]) -> PaperOrder:
        columns = [key for key in values if key in _COLUMNS and key != "id"]
        placeholders = ",".join("?" for _ in columns)
        with db.session(self.path) as connection:
            cursor = connection.execute(
                f"INSERT INTO kagit_emirleri ({','.join(columns)}) VALUES ({placeholders})",
                tuple(values[key] for key in columns),
            )
            order_id = int(cursor.lastrowid or 0)
        found = self.get(order_id)
        assert found is not None
        return found

    def update(self, order_id: int, changes: dict[str, Any]) -> PaperOrder:
        columns = [key for key in changes if key in _COLUMNS and key != "id"]
        if columns:
            assignments = ", ".join(f"{key} = ?" for key in columns)
            with db.session(self.path) as connection:
                connection.execute(
                    f"UPDATE kagit_emirleri SET {assignments} WHERE id = ?",
                    (*[changes[key] for key in columns], int(order_id)),
                )
        found = self.get(order_id)
        assert found is not None
        return found

    def get(self, order_id: int) -> PaperOrder | None:
        with db.session(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM kagit_emirleri WHERE id = ?", (int(order_id),)
            ).fetchone()
        return PaperOrder(**dict(row)) if row is not None else None

    def _select(self, where: str = "", params: tuple[Any, ...] = (), *,
                order: str = "id", limit: int | None = None) -> tuple[PaperOrder, ...]:
        query = "SELECT * FROM kagit_emirleri"
        if where:
            query += f" WHERE {where}"
        query += f" ORDER BY {order}"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        with db.session(self.path) as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(PaperOrder(**dict(row)) for row in rows)

    def active(self) -> tuple[PaperOrder, ...]:
        """Bekleyen giriş emirleri ve açık pozisyonlar (bütün dönemler)."""
        return self._select("durum IN (?, ?)", (STATUS_PENDING, STATUS_OPEN))

    def in_period(self, donem_id: int) -> tuple[PaperOrder, ...]:
        return self._select("donem_id = ?", (int(donem_id),))

    def closed_in_period(self, donem_id: int) -> tuple[PaperOrder, ...]:
        return self._select(
            "donem_id = ? AND durum = ?", (int(donem_id), STATUS_CLOSED), order="cikis_utc, id"
        )

    def recent(self, limit: int = 100) -> tuple[PaperOrder, ...]:
        return self._select(order="id DESC", limit=limit)

    def all_closed(self) -> tuple[PaperOrder, ...]:
        return self._select("durum = ?", (STATUS_CLOSED,), order="cikis_utc, id")

    # --- küçük durum değerleri ---------------------------------------------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with db.session(self.path) as connection:
            row = connection.execute(
                "SELECT deger FROM kagit_durum WHERE anahtar = ?", (key,)
            ).fetchone()
        return row["deger"] if row is not None else default

    def set_state(self, key: str, value: str) -> None:
        with db.session(self.path) as connection:
            connection.execute(
                "INSERT INTO kagit_durum (anahtar, deger) VALUES (?, ?) "
                "ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger",
                (key, value),
            )

    def get_json(self, key: str, default: Any) -> Any:
        text = self.get_state(key)
        if text is None:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return default

    def set_json(self, key: str, value: Any) -> None:
        self.set_state(key, json.dumps(value, ensure_ascii=False))


__all__ = [
    "CLIENT_ID_PREFIX",
    "STATUS_CANCELLED",
    "STATUS_CLOSED",
    "STATUS_LABELS_TR",
    "STATUS_OPEN",
    "STATUS_PENDING",
    "PaperLedger",
    "PaperOrder",
    "new_client_id",
]
