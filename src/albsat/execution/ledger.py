"""Demo Mode emir ve pozisyon kayıtları (SQLite, ``albsat.sqlite3``).

Kâğıt işlemde bir emrin bütün hayatı tek satırdı. Gerçek borsada öyle
değil: bir pozisyon birkaç emirden (giriş, hedef, stop, yeniden kurulan
koruma, korumalı çıkış) ve her emir birkaç dolumdan oluşabilir (kısmi
dolum). Bu yüzden üç tablo var:

* ``demo_pozisyonlar``: bir işlem fikri (giriş, hedef, stop, büyüklük) ve
  yaşam durumu.
* ``demo_emirler``: borsaya giden her emir, **gönderilmeden önce** yazılır
  (kimliği, rolü, fiyatı, borsadaki son durumu).
* ``demo_dolumlar``: borsanın bildirdiği her dolum, borsanın işlem
  kimliğiyle bir kez (aynı dolum akıştan ve REST'ten iki kez gelse de).

Bakiye ayrı tutulmaz; dolumlardan hesaplanır (kâğıt defterindeki ilke).
Borsadaki gerçek bakiye uzlaştırmada bununla karşılaştırılır.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from albsat.core import db
from albsat.core.clock import iso, parse_utc, utc_now
from albsat.core.money import ONE_HUNDRED, ZERO

# --- pozisyon durumları ---------------------------------------------------------

POS_SENDING = "gonderiliyor"
POS_UNKNOWN = "belirsiz"
POS_PENDING = "bekliyor"
POS_PARTIAL = "kismi"
POS_PROTECTED = "korunuyor"
POS_UNPROTECTED = "korumasiz"
POS_EXITING = "cikiliyor"
POS_CLOSED = "kapandi"
POS_CANCELLED = "iptal"
POS_REJECTED = "reddedildi"

POS_FINAL = frozenset({POS_CLOSED, POS_CANCELLED, POS_REJECTED})

POS_LABELS_TR = {
    POS_SENDING: "gönderiliyor",
    POS_UNKNOWN: "sonucu sorgulanıyor",
    POS_PENDING: "giriş bekliyor",
    POS_PARTIAL: "kısmen doldu",
    POS_PROTECTED: "açık, stop ve hedef borsada",
    POS_UNPROTECTED: "açık, KORUMASIZ",
    POS_EXITING: "kapatılıyor",
    POS_CLOSED: "kapandı",
    POS_CANCELLED: "iptal edildi",
    POS_REJECTED: "borsa reddetti",
}

# --- emir (bacak) durumları ----------------------------------------------------------

LEG_SENDING = "GONDERILIYOR"
LEG_UNKNOWN = "BILINMIYOR"
LEG_NEVER = "ULASMADI"

LEG_LIVE = frozenset({"NEW", "PARTIALLY_FILLED", "PENDING_NEW", "PENDING_CANCEL"})
LEG_FINAL = frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH",
                       LEG_NEVER})
LEG_UNRESOLVED = frozenset({LEG_SENDING, LEG_UNKNOWN})

ROLE_ENTRY = "giris"
ROLE_TARGET = "hedef"
ROLE_STOP = "stop"
ROLE_EXIT = "cikis"

ROLE_LABELS_TR = {
    ROLE_ENTRY: "giriş",
    ROLE_TARGET: "hedef",
    ROLE_STOP: "stop",
    ROLE_EXIT: "korumalı çıkış",
}

# --- çıkış sebepleri --------------------------------------------------------------

EXIT_TARGET = "hedef"
EXIT_STOP = "stop"
EXIT_TIME = "sure"
EXIT_KILL = "acil_durdur"
EXIT_MANUAL = "elle_kapatma"
EXIT_PROTECT = "koruma_cikisi"
EXIT_DUST = "kusurat"

EXIT_LABELS_TR = {
    EXIT_TARGET: "hedefe ulaştı",
    EXIT_STOP: "stop oldu",
    EXIT_TIME: "süre doldu",
    EXIT_KILL: "acil durdurmada kapatıldı",
    EXIT_MANUAL: "elle kapatıldı",
    EXIT_PROTECT: "koruma kurulamadığı için satıldı",
    EXIT_DUST: "satılamayacak kadar küçük kaldı",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_hesap_donemleri (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    baslangic_utc   TEXT NOT NULL,
    baslangic_usdt  TEXT NOT NULL,
    bitis_utc       TEXT
);

CREATE TABLE IF NOT EXISTS demo_pozisyonlar (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    donem_id                  INTEGER NOT NULL REFERENCES demo_hesap_donemleri(id),
    jeton                     TEXT NOT NULL UNIQUE,
    olusturma_utc             TEXT NOT NULL,
    sembol                    TEXT NOT NULL,
    periyot                   TEXT NOT NULL,
    kaynak                    TEXT NOT NULL,
    kural_kimligi             TEXT,
    kural_etiketi             TEXT,
    sinyal_mumu_utc           TEXT,
    giris                     TEXT NOT NULL,
    hedef                     TEXT NOT NULL,
    stop                      TEXT NOT NULL,
    stop_limit                TEXT,
    miktar                    TEXT NOT NULL,
    tutar_usdt                TEXT NOT NULL,
    stop_zarari_usdt          TEXT NOT NULL,
    bekleyen_miktar           TEXT NOT NULL,
    onceki_toz                TEXT NOT NULL DEFAULT '0',
    gecerlilik_bitis_ms       INTEGER NOT NULL,
    azami_tutma_ms            INTEGER NOT NULL,
    beklenen_hedef_net_yuzde  REAL,
    beklenen_ortalama_yuzde   REAL,
    yeniden_fiyatlama         INTEGER NOT NULL DEFAULT 0,
    durum                     TEXT NOT NULL,
    aciklama                  TEXT,
    koruma_denemesi           INTEGER NOT NULL DEFAULT 0,
    cikis_denemesi            INTEGER NOT NULL DEFAULT 0,
    cikis_istegi              TEXT,
    giris_iptal_istegi        TEXT,
    ilk_dolum_ms              INTEGER,
    korumasiz_baslangic_ms    INTEGER,
    korumasiz_toplam_ms       INTEGER NOT NULL DEFAULT 0,
    korumasiz_azami_ms        INTEGER NOT NULL DEFAULT 0,
    kapanis_utc               TEXT,
    cikis_sebebi              TEXT,
    cikis_fiyati              TEXT,
    maliyet_usdt              TEXT,
    gelir_usdt                TEXT,
    komisyon_usdt             TEXT,
    toz_degisimi              TEXT,
    net_usdt                  TEXT,
    net_yuzde                 REAL,
    usdttry                   TEXT,
    notlar                    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS demo_pozisyonlar_durum ON demo_pozisyonlar (durum);

CREATE TABLE IF NOT EXISTS demo_emirler (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    pozisyon_id           INTEGER NOT NULL REFERENCES demo_pozisyonlar(id),
    istemci_kimligi       TEXT NOT NULL UNIQUE,
    liste_istemci_kimligi TEXT,
    rol                   TEXT NOT NULL,
    tur                   TEXT NOT NULL,
    taraf                 TEXT NOT NULL,
    fiyat                 TEXT,
    stop_fiyati           TEXT,
    miktar                TEXT NOT NULL,
    durum                 TEXT NOT NULL,
    borsa_emir_kimligi    INTEGER,
    borsa_liste_kimligi   INTEGER,
    dolan                 TEXT NOT NULL DEFAULT '0',
    dolan_quote           TEXT NOT NULL DEFAULT '0',
    gonderim_ms           INTEGER,
    sebep                 TEXT,
    olusturma_utc         TEXT NOT NULL,
    guncelleme_utc        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS demo_emirler_pozisyon ON demo_emirler (pozisyon_id);

CREATE TABLE IF NOT EXISTS demo_dolumlar (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pozisyon_id       INTEGER NOT NULL REFERENCES demo_pozisyonlar(id),
    emir_istemci      TEXT NOT NULL,
    sembol            TEXT NOT NULL,
    taraf             TEXT NOT NULL,
    islem_kimligi     INTEGER NOT NULL,
    fiyat             TEXT NOT NULL,
    miktar            TEXT NOT NULL,
    quote_miktar      TEXT NOT NULL,
    komisyon          TEXT NOT NULL,
    komisyon_varligi  TEXT,
    maker             INTEGER NOT NULL DEFAULT 0,
    zaman_ms          INTEGER NOT NULL,
    kaynak            TEXT NOT NULL,
    UNIQUE (sembol, islem_kimligi)
);
CREATE INDEX IF NOT EXISTS demo_dolumlar_pozisyon ON demo_dolumlar (pozisyon_id);

CREATE TABLE IF NOT EXISTS demo_durum (
    anahtar   TEXT PRIMARY KEY,
    deger     TEXT NOT NULL
);
"""


def _dec(value: str | None) -> Decimal:
    return Decimal(value) if value not in (None, "") else ZERO


@dataclass(frozen=True)
class DemoPosition:
    id: int
    donem_id: int
    jeton: str
    olusturma_utc: str
    sembol: str
    periyot: str
    kaynak: str
    kural_kimligi: str | None
    kural_etiketi: str | None
    sinyal_mumu_utc: str | None
    giris: str
    hedef: str
    stop: str
    stop_limit: str | None
    miktar: str
    tutar_usdt: str
    stop_zarari_usdt: str
    bekleyen_miktar: str
    onceki_toz: str
    gecerlilik_bitis_ms: int
    azami_tutma_ms: int
    beklenen_hedef_net_yuzde: float | None
    beklenen_ortalama_yuzde: float | None
    yeniden_fiyatlama: int
    durum: str
    aciklama: str | None
    koruma_denemesi: int
    cikis_denemesi: int
    cikis_istegi: str | None
    giris_iptal_istegi: str | None
    ilk_dolum_ms: int | None
    korumasiz_baslangic_ms: int | None
    korumasiz_toplam_ms: int
    korumasiz_azami_ms: int
    kapanis_utc: str | None
    cikis_sebebi: str | None
    cikis_fiyati: str | None
    maliyet_usdt: str | None
    gelir_usdt: str | None
    komisyon_usdt: str | None
    toz_degisimi: str | None
    net_usdt: str | None
    net_yuzde: float | None
    usdttry: str | None
    notlar: str

    def dec(self, name: str) -> Decimal:
        return _dec(getattr(self, name))

    @property
    def not_listesi(self) -> list[str]:
        try:
            items = json.loads(self.notlar or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item) for item in items] if isinstance(items, list) else []

    @property
    def durum_tr(self) -> str:
        return POS_LABELS_TR.get(self.durum, self.durum)

    @property
    def bitti(self) -> bool:
        return self.durum in POS_FINAL

    @property
    def kapanis(self) -> datetime | None:
        return parse_utc(self.kapanis_utc)


@dataclass(frozen=True)
class DemoLeg:
    id: int
    pozisyon_id: int
    istemci_kimligi: str
    liste_istemci_kimligi: str | None
    rol: str
    tur: str
    taraf: str
    fiyat: str | None
    stop_fiyati: str | None
    miktar: str
    durum: str
    borsa_emir_kimligi: int | None
    borsa_liste_kimligi: int | None
    dolan: str
    dolan_quote: str
    gonderim_ms: int | None
    sebep: str | None
    olusturma_utc: str
    guncelleme_utc: str

    def dec(self, name: str) -> Decimal:
        return _dec(getattr(self, name))

    @property
    def canli(self) -> bool:
        return self.durum in LEG_LIVE

    @property
    def bitti(self) -> bool:
        return self.durum in LEG_FINAL

    @property
    def belirsiz(self) -> bool:
        return self.durum in LEG_UNRESOLVED

    @property
    def rol_tr(self) -> str:
        return ROLE_LABELS_TR.get(self.rol, self.rol)


@dataclass(frozen=True)
class DemoFill:
    id: int
    pozisyon_id: int
    emir_istemci: str
    sembol: str
    taraf: str
    islem_kimligi: int
    fiyat: str
    miktar: str
    quote_miktar: str
    komisyon: str
    komisyon_varligi: str | None
    maker: int
    zaman_ms: int
    kaynak: str

    def dec(self, name: str) -> Decimal:
        return _dec(getattr(self, name))


_POS_COLUMNS = tuple(item.name for item in fields(DemoPosition))
_LEG_COLUMNS = tuple(item.name for item in fields(DemoLeg))
_FILL_COLUMNS = tuple(item.name for item in fields(DemoFill))


@dataclass(frozen=True)
class Economics:
    """Bir pozisyonun dolumlarından hesaplanan tutarlar (USDT ve coin)."""

    alinan_brut: Decimal
    alis_quote: Decimal
    #: Alışta coinden kesilen komisyon.
    alis_komisyon_coin: Decimal
    satilan: Decimal
    satis_quote: Decimal
    #: USDT cinsinden kesilen ya da USDT'ye çevrilen komisyonlar.
    komisyon_usdt: Decimal
    #: Değeri bilinemeyen komisyon varlıkları (ör. BNB fiyatı yoksa).
    degerlenemeyen: tuple[str, ...]
    son_satis_ms: int | None

    @property
    def alinan(self) -> Decimal:
        """Hesaba giren coin: alınan − alışta coinden kesilen komisyon."""
        return self.alinan_brut - self.alis_komisyon_coin

    @property
    def elde(self) -> Decimal:
        """Bu pozisyonun elde kalan coini (önceki toz hariç)."""
        return self.alinan - self.satilan

    @property
    def ortalama_giris(self) -> Decimal | None:
        return self.alis_quote / self.alinan_brut if self.alinan_brut > ZERO else None

    @property
    def ortalama_cikis(self) -> Decimal | None:
        return self.satis_quote / self.satilan if self.satilan > ZERO else None


def economics(
    fills: Iterable[DemoFill],
    *,
    base_asset: str,
    quote_asset: str = "USDT",
    price_of: Callable[[str], Decimal | None] = lambda asset: None,
) -> Economics:
    bought = spent = fee_coin = sold = received_quote = fee_usdt = ZERO
    unknown: set[str] = set()
    last_sell: int | None = None
    for fill in fills:
        quantity = fill.dec("miktar")
        quote = fill.dec("quote_miktar")
        fee = fill.dec("komisyon")
        asset = fill.komisyon_varligi or ""
        if fill.taraf == "BUY":
            bought += quantity
            spent += quote
        else:
            sold += quantity
            received_quote += quote
            last_sell = fill.zaman_ms if last_sell is None else max(last_sell, fill.zaman_ms)
        if fee == ZERO:
            continue
        if asset == base_asset:
            if fill.taraf == "BUY":
                fee_coin += fee
            else:  # satışta coinden kesilirse satılan miktara eklenir
                sold += fee
        elif asset == quote_asset:
            fee_usdt += fee
        else:
            price = price_of(asset)
            if price is None:
                unknown.add(asset)
            else:
                fee_usdt += fee * price
    return Economics(
        alinan_brut=bought,
        alis_quote=spent,
        alis_komisyon_coin=fee_coin,
        satilan=sold,
        satis_quote=received_quote,
        komisyon_usdt=fee_usdt,
        degerlenemeyen=tuple(sorted(unknown)),
        son_satis_ms=last_sell,
    )


def net_result(econ: Economics, *, exit_price: Decimal | None) -> tuple[Decimal, Decimal, Decimal]:
    """(maliyet, net USDT, net yüzde). Kâğıt işlemdeki kuralla aynı: bu işlemin toz
    bakiyesinde yaptığı değişiklik (alınan − satılan) çıkış fiyatından değerlenir."""
    cost = econ.alis_quote
    dust_change = econ.alinan - econ.satilan
    value = dust_change * exit_price if exit_price is not None else ZERO
    net = econ.satis_quote - econ.komisyon_usdt - cost + value
    pct = net / cost * ONE_HUNDRED if cost > ZERO else ZERO
    return cost, net, pct


class DemoLedger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        db.ensure_schema(self.path, _SCHEMA)

    @classmethod
    def in_directory(cls, root: Path | str) -> DemoLedger:
        return cls(db.path_in(root))

    # --- hesap dönemi ------------------------------------------------------

    def current_period(self, *, default_start_usdt: Decimal) -> tuple[int, Decimal, str]:
        with db.session(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM demo_hesap_donemleri WHERE bitis_utc IS NULL "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                now = iso(utc_now())
                cursor = connection.execute(
                    "INSERT INTO demo_hesap_donemleri (baslangic_utc, baslangic_usdt) "
                    "VALUES (?, ?)", (now, str(default_start_usdt)),
                )
                return int(cursor.lastrowid or 0), default_start_usdt, now
            return int(row["id"]), Decimal(row["baslangic_usdt"]), row["baslangic_utc"]

    def reset_period(self, *, start_usdt: Decimal, now: datetime) -> int:
        with db.session(self.path) as connection:
            connection.execute(
                "UPDATE demo_hesap_donemleri SET bitis_utc = ? WHERE bitis_utc IS NULL",
                (iso(now),),
            )
            cursor = connection.execute(
                "INSERT INTO demo_hesap_donemleri (baslangic_utc, baslangic_usdt) VALUES (?, ?)",
                (iso(now), str(start_usdt)),
            )
            return int(cursor.lastrowid or 0)

    # --- pozisyonlar ---------------------------------------------------------

    def insert_position(self, values: dict[str, Any], legs: list[dict[str, Any]]) -> DemoPosition:
        """Pozisyonu ve emirlerini tek işlemde yazar (gönderimden önce)."""
        columns = [key for key in values if key in _POS_COLUMNS and key != "id"]
        now = iso(utc_now())
        with db.session(self.path) as connection:
            cursor = connection.execute(
                f"INSERT INTO demo_pozisyonlar ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(values[key] for key in columns),
            )
            position_id = int(cursor.lastrowid or 0)
            for leg in legs:
                self._insert_leg(connection, position_id, leg, now)
        found = self.position(position_id)
        assert found is not None
        return found

    def update_position(self, position_id: int, changes: dict[str, Any]) -> DemoPosition:
        columns = [key for key in changes if key in _POS_COLUMNS and key != "id"]
        if columns:
            with db.session(self.path) as connection:
                connection.execute(
                    f"UPDATE demo_pozisyonlar SET {', '.join(f'{key} = ?' for key in columns)} "
                    "WHERE id = ?",
                    (*[changes[key] for key in columns], int(position_id)),
                )
        found = self.position(position_id)
        assert found is not None
        return found

    def position(self, position_id: int) -> DemoPosition | None:
        rows = self._positions("id = ?", (int(position_id),))
        return rows[0] if rows else None

    def position_by_token(self, token: str) -> DemoPosition | None:
        rows = self._positions("jeton = ?", (token,))
        return rows[0] if rows else None

    def _positions(self, where: str = "", params: tuple[Any, ...] = (), *,
                   order: str = "id", limit: int | None = None) -> tuple[DemoPosition, ...]:
        query = "SELECT * FROM demo_pozisyonlar"
        if where:
            query += f" WHERE {where}"
        query += f" ORDER BY {order}"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        with db.session(self.path) as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(DemoPosition(**dict(row)) for row in rows)

    def active(self) -> tuple[DemoPosition, ...]:
        placeholders = ",".join("?" for _ in POS_FINAL)
        return self._positions(f"durum NOT IN ({placeholders})", tuple(POS_FINAL))

    def in_period(self, donem_id: int) -> tuple[DemoPosition, ...]:
        return self._positions("donem_id = ?", (int(donem_id),))

    def closed_in_period(self, donem_id: int) -> tuple[DemoPosition, ...]:
        return self._positions("donem_id = ? AND durum = ?", (int(donem_id), POS_CLOSED),
                               order="kapanis_utc, id")

    def all_closed(self) -> tuple[DemoPosition, ...]:
        return self._positions("durum = ?", (POS_CLOSED,), order="kapanis_utc, id")

    def recent(self, limit: int = 100) -> tuple[DemoPosition, ...]:
        return self._positions(order="id DESC", limit=limit)

    # --- emirler -------------------------------------------------------------

    @staticmethod
    def _insert_leg(connection: Any, position_id: int, leg: dict[str, Any], now: str) -> None:
        values = {**leg, "pozisyon_id": position_id, "olusturma_utc": now,
                  "guncelleme_utc": now}
        columns = [key for key in values if key in _LEG_COLUMNS and key != "id"]
        connection.execute(
            f"INSERT INTO demo_emirler ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            tuple(values[key] for key in columns),
        )

    def add_legs(self, position_id: int, legs: list[dict[str, Any]]) -> None:
        now = iso(utc_now())
        with db.session(self.path) as connection:
            for leg in legs:
                self._insert_leg(connection, position_id, leg, now)

    def update_leg(self, client_id: str, changes: dict[str, Any]) -> DemoLeg | None:
        columns = [key for key in changes if key in _LEG_COLUMNS and key != "id"]
        if columns:
            values = [changes[key] for key in columns]
            with db.session(self.path) as connection:
                connection.execute(
                    f"UPDATE demo_emirler SET {', '.join(f'{key} = ?' for key in columns)}, "
                    "guncelleme_utc = ? WHERE istemci_kimligi = ?",
                    (*values, iso(utc_now()), client_id),
                )
        return self.leg(client_id)

    def leg(self, client_id: str) -> DemoLeg | None:
        with db.session(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM demo_emirler WHERE istemci_kimligi = ?", (client_id,)
            ).fetchone()
        return DemoLeg(**dict(row)) if row is not None else None

    def legs(self, position_id: int) -> tuple[DemoLeg, ...]:
        with db.session(self.path) as connection:
            rows = connection.execute(
                "SELECT * FROM demo_emirler WHERE pozisyon_id = ? ORDER BY id",
                (int(position_id),),
            ).fetchall()
        return tuple(DemoLeg(**dict(row)) for row in rows)

    def open_legs(self) -> tuple[DemoLeg, ...]:
        """Bitmemiş (canlı ya da sonucu bilinmeyen) bütün emirler."""
        placeholders = ",".join("?" for _ in LEG_FINAL)
        with db.session(self.path) as connection:
            rows = connection.execute(
                f"SELECT * FROM demo_emirler WHERE durum NOT IN ({placeholders}) ORDER BY id",
                tuple(LEG_FINAL),
            ).fetchall()
        return tuple(DemoLeg(**dict(row)) for row in rows)

    # --- dolumlar --------------------------------------------------------------

    def add_fill(self, values: dict[str, Any]) -> bool:
        """Dolumu yazar; aynı işlem kimliği zaten varsa ``False``."""
        columns = [key for key in values if key in _FILL_COLUMNS and key != "id"]
        with db.session(self.path) as connection:
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO demo_dolumlar ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(values[key] for key in columns),
            )
            return bool(cursor.rowcount)

    def fills(self, position_id: int) -> tuple[DemoFill, ...]:
        with db.session(self.path) as connection:
            rows = connection.execute(
                "SELECT * FROM demo_dolumlar WHERE pozisyon_id = ? ORDER BY zaman_ms, id",
                (int(position_id),),
            ).fetchall()
        return tuple(DemoFill(**dict(row)) for row in rows)

    def filled_for_leg(self, client_id: str) -> Decimal:
        with db.session(self.path) as connection:
            rows = connection.execute(
                "SELECT miktar FROM demo_dolumlar WHERE emir_istemci = ?", (client_id,)
            ).fetchall()
        return sum((Decimal(row["miktar"]) for row in rows), ZERO)

    # --- küçük durum değerleri ----------------------------------------------------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with db.session(self.path) as connection:
            row = connection.execute(
                "SELECT deger FROM demo_durum WHERE anahtar = ?", (key,)
            ).fetchone()
        return row["deger"] if row is not None else default

    def set_state(self, key: str, value: str) -> None:
        with db.session(self.path) as connection:
            connection.execute(
                "INSERT INTO demo_durum (anahtar, deger) VALUES (?, ?) "
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
    "EXIT_DUST",
    "EXIT_KILL",
    "EXIT_LABELS_TR",
    "EXIT_MANUAL",
    "EXIT_PROTECT",
    "EXIT_STOP",
    "EXIT_TARGET",
    "EXIT_TIME",
    "LEG_FINAL",
    "LEG_LIVE",
    "LEG_NEVER",
    "LEG_SENDING",
    "LEG_UNKNOWN",
    "LEG_UNRESOLVED",
    "POS_CANCELLED",
    "POS_CLOSED",
    "POS_EXITING",
    "POS_FINAL",
    "POS_LABELS_TR",
    "POS_PARTIAL",
    "POS_PENDING",
    "POS_PROTECTED",
    "POS_REJECTED",
    "POS_SENDING",
    "POS_UNKNOWN",
    "POS_UNPROTECTED",
    "ROLE_ENTRY",
    "ROLE_EXIT",
    "ROLE_LABELS_TR",
    "ROLE_STOP",
    "ROLE_TARGET",
    "DemoFill",
    "DemoLedger",
    "DemoLeg",
    "DemoPosition",
    "Economics",
    "economics",
    "net_result",
]
