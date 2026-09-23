"""Coin başına çalışma modu (SPEC.md §4.7).

Faz 4'te seçilebilen modlar: **Kapalı**, **Sadece Öneri**, **Kâğıt İşlem**.
Demo, Yarı Otomatik ve Tam Otomatik listede görünür ama kilitlidir; emir
yürütme Faz 5'te, canlı işlem Faz 6'da gelir. Kilitli bir moda geçmeye
çalışmak reddedilir ve nedeni söylenir.

**Uygulama her açılışta Sadece Öneri modunda başlar** (SPEC §2, sabit
karar). Bir önceki oturumda kâğıt işlemde olan coin, yeniden açılışta
kendiliğinden kâğıt işleme dönmez; ``previous`` o bilgiyi arayüzün "önceki
oturumda kâğıt işlemdeydi, devam etmek için açın" diyebilmesi için saklar.
Borsada (burada: kâğıt defterde) duran emirler ve pozisyonlar moddan
bağımsız olarak izlenmeye devam eder; gerçek bir borsada da uygulama
kapanınca emirler silinmez.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from albsat.core import db
from albsat.core.clock import iso, utc_now

MODE_OFF = "kapali"
MODE_ADVICE = "sadece_oneri"
MODE_PAPER = "kagit"
MODE_DEMO = "demo"
MODE_SEMI = "yari_otomatik"
MODE_FULL = "tam_otomatik"

MODE_LABELS_TR = {
    MODE_OFF: "Kapalı",
    MODE_ADVICE: "Sadece Öneri",
    MODE_PAPER: "Kâğıt İşlem",
    MODE_DEMO: "Demo Mode",
    MODE_SEMI: "Yarı Otomatik",
    MODE_FULL: "Tam Otomatik",
}

#: Bu fazda seçilebilen modlar.
SELECTABLE = (MODE_OFF, MODE_ADVICE, MODE_PAPER)

#: Kilitli modlar ve hangi fazda açılacakları.
LOCKED = {
    MODE_DEMO: "Faz 5 (emir yürütme, Binance Demo Mode)",
    MODE_SEMI: "Faz 6 (canlı, her emir için onay)",
    MODE_FULL: "Faz 6 (canlı, canlıya geçiş kapısından sonra)",
}

DEFAULT_MODE = MODE_ADVICE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS modlar (
    sembol           TEXT PRIMARY KEY,
    mod              TEXT NOT NULL,
    onceki_oturum    TEXT,
    guncelleme_utc   TEXT NOT NULL
);
"""


class ModeError(ValueError):
    """Geçersiz ya da kilitli mod; mesaj kullanıcıya gösterilir."""


@dataclass(frozen=True)
class CoinMode:
    sembol: str
    mod: str
    #: Uygulama açılmadan önceki oturumda hangi moddaydı (bilgi amaçlı).
    onceki_oturum: str | None

    @property
    def mod_tr(self) -> str:
        return MODE_LABELS_TR.get(self.mod, self.mod)


class ModeStore:
    def __init__(self, path: Path | str, symbols: Sequence[str]) -> None:
        self.path = Path(path)
        self.symbols = tuple(symbols)
        db.ensure_schema(self.path, _SCHEMA)

    @classmethod
    def in_directory(cls, root: Path | str, symbols: Sequence[str]) -> ModeStore:
        return cls(db.path_in(root), symbols)

    def start_session(self) -> tuple[CoinMode, ...]:
        """Açılışta her coini Sadece Öneri'ye çeker; eski modu not eder."""
        now = iso(utc_now())
        with db.session(self.path) as connection:
            for symbol in self.symbols:
                row = connection.execute(
                    "SELECT mod FROM modlar WHERE sembol = ?", (symbol,)
                ).fetchone()
                previous = row["mod"] if row is not None else None
                connection.execute(
                    "INSERT INTO modlar (sembol, mod, onceki_oturum, guncelleme_utc) "
                    "VALUES (?,?,?,?) ON CONFLICT(sembol) DO UPDATE SET mod = excluded.mod, "
                    "onceki_oturum = excluded.onceki_oturum, "
                    "guncelleme_utc = excluded.guncelleme_utc",
                    (symbol, DEFAULT_MODE, previous, now),
                )
        return self.all()

    def all(self) -> tuple[CoinMode, ...]:
        with db.session(self.path) as connection:
            rows = {
                row["sembol"]: row
                for row in connection.execute("SELECT * FROM modlar").fetchall()
            }
        return tuple(
            CoinMode(
                sembol=symbol,
                mod=rows[symbol]["mod"] if symbol in rows else DEFAULT_MODE,
                onceki_oturum=rows[symbol]["onceki_oturum"] if symbol in rows else None,
            )
            for symbol in self.symbols
        )

    def get(self, symbol: str) -> str:
        for item in self.all():
            if item.sembol == symbol:
                return item.mod
        return MODE_OFF

    def set(self, symbol: str, mode: str) -> tuple[str, str]:
        """Modu değiştirir; (eski, yeni) döner. Kilitli/bilinmeyen mod reddedilir."""
        if symbol not in self.symbols:
            raise ModeError(f"{symbol} izlenen coinler arasında değil.")
        if mode in LOCKED:
            raise ModeError(
                f"{MODE_LABELS_TR[mode]} bu fazda kilitli; {LOCKED[mode]} ile açılacak."
            )
        if mode not in SELECTABLE:
            raise ModeError(f"Bilinmeyen mod: {mode}")
        old = self.get(symbol)
        with db.session(self.path) as connection:
            connection.execute(
                "INSERT INTO modlar (sembol, mod, onceki_oturum, guncelleme_utc) "
                "VALUES (?,?,NULL,?) ON CONFLICT(sembol) DO UPDATE SET mod = excluded.mod, "
                "guncelleme_utc = excluded.guncelleme_utc",
                (symbol, mode, iso(utc_now())),
            )
        return old, mode

    def paper_symbols(self) -> tuple[str, ...]:
        return tuple(item.sembol for item in self.all() if item.mod == MODE_PAPER)


__all__ = [
    "DEFAULT_MODE",
    "LOCKED",
    "MODE_ADVICE",
    "MODE_DEMO",
    "MODE_FULL",
    "MODE_LABELS_TR",
    "MODE_OFF",
    "MODE_PAPER",
    "MODE_SEMI",
    "SELECTABLE",
    "CoinMode",
    "ModeError",
    "ModeStore",
]
