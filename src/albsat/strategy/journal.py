"""Sinyal günlüğü: "önerilen vs gerçekleşen" (SPEC.md §4.4).

Her öneri kartı, üretildiği anda buraya yazılır. Geçerlilik penceresi
kapandıktan sonra aynı mum verisinden gerçekte ne olduğu hesaplanır ve satır
tamamlanır. Böylece uygulamanın önerilerinin tutup tutmadığı, hafızaya veya
iyi niyete değil kayda dayanır.

Depolama SQLite (SPEC §3: sinyaller ve denetim kaydı SQLite, mum verisi
Parquet). Tek dosya, tek tablo; Faz 4 kağıt işlem kayıtlarını yanına ekler.

**Tekillik kuralı:** bir kural aynı sinyal mumunda iki kez kaydedilmez.
Arayüz her açılışta önerileri yeniden hesapladığı için bu şart; aksi hâlde
sayfa yenilendikçe aynı sinyal onlarca kez günlüğe düşerdi.

Gerçekleşen sonuç, olay çalışmasının **aynı temkinli varsayımlarıyla**
hesaplanır: aynı mumda hem hedefe hem stopa değilirse stop kabul edilir,
çünkü mum verisi hangisinin önce geldiğini söylemez.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from albsat.core.costs import RoundTrip
from albsat.data.klines import closed_only
from albsat.data.store import KlineStore
from albsat.strategy.card import SignalCard

DEFAULT_FILENAME = "albsat.sqlite3"

STATUS_OPEN = "acik"
STATUS_HIT_TARGET = "hedef"
STATUS_HIT_STOP = "stop"
STATUS_TIMEOUT = "sure_doldu"

STATUS_LABELS_TR = {
    STATUS_OPEN: "açık",
    STATUS_HIT_TARGET: "hedefe ulaştı",
    STATUS_HIT_STOP: "stopa düştü",
    STATUS_TIMEOUT: "süre doldu",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sinyal_gunlugu (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    kayit_zamani_utc      TEXT NOT NULL,
    kural_kimligi         TEXT NOT NULL,
    sembol                TEXT NOT NULL,
    periyot               TEXT NOT NULL,
    aksiyon               TEXT NOT NULL,
    sinyal_mumu_utc       TEXT NOT NULL,
    gecerlilik_bitis_utc  TEXT NOT NULL,
    pencere_mum           INTEGER NOT NULL,
    giris                 TEXT NOT NULL,
    hedef1                TEXT NOT NULL,
    stop                  TEXT NOT NULL,
    basa_bas              TEXT NOT NULL,
    onerilen_net_yuzde    REAL NOT NULL,
    guven_puani           INTEGER NOT NULL,
    miktar                TEXT NOT NULL,
    tutar_usdt            TEXT NOT NULL,
    stop_zarari_usdt      TEXT NOT NULL,
    durum                 TEXT NOT NULL,
    gerceklesen_net_yuzde REAL,
    sonuclanma_utc        TEXT,
    UNIQUE (kural_kimligi, sinyal_mumu_utc)
);
"""


@dataclass(frozen=True)
class JournalEntry:
    """Günlükteki tek satır."""

    id: int
    kayit_zamani_utc: str
    kural_kimligi: str
    sembol: str
    periyot: str
    aksiyon: str
    sinyal_mumu_utc: str
    gecerlilik_bitis_utc: str
    pencere_mum: int
    giris: str
    hedef1: str
    stop: str
    basa_bas: str
    onerilen_net_yuzde: float
    guven_puani: int
    miktar: str
    tutar_usdt: str
    stop_zarari_usdt: str
    durum: str
    gerceklesen_net_yuzde: float | None
    sonuclanma_utc: str | None

    @property
    def durum_tr(self) -> str:
        return STATUS_LABELS_TR.get(self.durum, self.durum)

    @property
    def fark_yuzde(self) -> float | None:
        """Gerçekleşen − önerilen. Sapma raporunun ham maddesi."""
        if self.gerceklesen_net_yuzde is None:
            return None
        return self.gerceklesen_net_yuzde - self.onerilen_net_yuzde


class SignalJournal:
    """Sinyal günlüğünün SQLite üzerindeki hali."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    @classmethod
    def in_directory(cls, root: Path | str, filename: str = DEFAULT_FILENAME) -> SignalJournal:
        return cls(Path(root) / filename)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    # --- yazma ---------------------------------------------------------

    def record(self, card: SignalCard, *, now: datetime | None = None) -> bool:
        """Kartı günlüğe yazar. Aynı kural + aynı sinyal mumu ikinci kez yazılmaz.

        Dönen değer yeni satır eklenip eklenmediğidir.
        """
        now = now or datetime.now(UTC)
        values = (
            now.replace(microsecond=0).isoformat(),
            card.kural_kimligi,
            card.sembol,
            card.periyot,
            card.aksiyon,
            card.sinyal_mumu_kapanis_utc,
            card.gecerlilik_bitis_utc,
            int(card.gecerlilik_mum),
            str(card.giris),
            str(card.hedef1),
            str(card.stop),
            str(card.basa_bas),
            float(card.net_marj_yuzde),
            int(card.guven.puan),
            str(card.pozisyon.miktar),
            str(card.pozisyon.tutar_usdt),
            str(card.pozisyon.stop_zarari_usdt),
            STATUS_OPEN,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO sinyal_gunlugu (
                    kayit_zamani_utc, kural_kimligi, sembol, periyot, aksiyon,
                    sinyal_mumu_utc, gecerlilik_bitis_utc, pencere_mum,
                    giris, hedef1, stop, basa_bas, onerilen_net_yuzde,
                    guven_puani, miktar, tutar_usdt, stop_zarari_usdt, durum
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            return cursor.rowcount > 0

    # --- okuma ---------------------------------------------------------

    def recent(self, limit: int = 50) -> tuple[JournalEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sinyal_gunlugu ORDER BY sinyal_mumu_utc DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return tuple(JournalEntry(**dict(row)) for row in rows)

    def open_entries(self) -> tuple[JournalEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sinyal_gunlugu WHERE durum = ? ORDER BY sinyal_mumu_utc",
                (STATUS_OPEN,),
            ).fetchall()
        return tuple(JournalEntry(**dict(row)) for row in rows)

    def performance(self) -> dict[str, float | int]:
        """Sonuçlanmış sinyallerin özeti (SPEC §4.8 sapma raporunun çekirdeği)."""
        settled = [
            item for item in self.recent(limit=10_000) if item.gerceklesen_net_yuzde is not None
        ]
        if not settled:
            return {"sonuclanan": 0}
        suggested = sum(item.onerilen_net_yuzde for item in settled) / len(settled)
        realized = sum(
            float(item.gerceklesen_net_yuzde or 0.0) for item in settled
        ) / len(settled)
        hits = sum(1 for item in settled if item.durum == STATUS_HIT_TARGET)
        return {
            "sonuclanan": len(settled),
            "onerilen_ortalama_yuzde": suggested,
            "gerceklesen_ortalama_yuzde": realized,
            "sapma_yuzde": realized - suggested,
            "hedefe_ulasan": hits,
            "isabet_orani": hits / len(settled),
        }

    # --- sonuçlandırma -------------------------------------------------

    def settle_from_candles(
        self,
        store: KlineStore,
        *,
        trip_to_target: RoundTrip,
        trip_to_stop: RoundTrip,
        exit_slippage_pct: float = 0.02,
        now: datetime | None = None,
    ) -> int:
        """Penceresi kapanmış sinyalleri mum verisinden sonuçlandırır.

        Olay çalışmasıyla aynı varsayımlar: giriş sinyal mumundan sonraki
        mumdan itibaren geçerlidir, aynı mumda hem hedef hem stop görülürse
        **stop** kabul edilir, stop ve süre dolumu piyasa emridir (kayma
        aleyhe işler).
        """
        now = now or datetime.now(UTC)
        settled = 0
        for entry in self.open_entries():
            deadline = _parse(entry.gecerlilik_bitis_utc)
            if deadline is None or deadline > now:
                continue
            outcome = _replay(
                entry,
                store=store,
                trip_to_target=trip_to_target,
                trip_to_stop=trip_to_stop,
                exit_slippage_pct=exit_slippage_pct,
            )
            if outcome is None:
                continue
            status, net = outcome
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE sinyal_gunlugu
                       SET durum = ?, gerceklesen_net_yuzde = ?, sonuclanma_utc = ?
                     WHERE id = ?
                    """,
                    (status, float(net), now.replace(microsecond=0).isoformat(), entry.id),
                )
            settled += 1
        return settled


def _parse(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _replay(
    entry: JournalEntry,
    *,
    store: KlineStore,
    trip_to_target: RoundTrip,
    trip_to_stop: RoundTrip,
    exit_slippage_pct: float,
) -> tuple[str, float] | None:
    """Sinyalden sonraki mumları okuyup gerçekte ne olduğunu bulur."""
    frame = store.read(entry.sembol, entry.periyot)
    if frame.empty:
        return None
    frame = closed_only(frame)
    signal_close = _parse(entry.sinyal_mumu_utc)
    if signal_close is None:
        return None
    signal_close_ms = int(signal_close.timestamp() * 1000)

    # Sinyal mumu, kapanış zamanı sinyalinkine eşit olan mumdur; pozisyon
    # ondan SONRAKİ mumla başlar.
    following = frame.loc[frame["open_time"] >= signal_close_ms]
    window = following.head(entry.pencere_mum)
    if len(window) < entry.pencere_mum:
        return None  # pencere henüz dolmamış; veri eksik olabilir

    entry_price = float(Decimal(entry.giris))
    target = float(Decimal(entry.hedef1))
    stop = float(Decimal(entry.stop))

    for _, candle in window.iterrows():
        low, high = float(candle["low"]), float(candle["high"])
        if low <= stop:
            fill = stop * (1.0 - exit_slippage_pct / 100.0)
            return STATUS_HIT_STOP, _net_pct(trip_to_stop, entry_price, fill)
        if high >= target:
            return STATUS_HIT_TARGET, _net_pct(trip_to_target, entry_price, target)

    last_close = float(window["close"].iloc[-1])
    fill = last_close * (1.0 - exit_slippage_pct / 100.0)
    return STATUS_TIMEOUT, _net_pct(trip_to_stop, entry_price, fill)


def _net_pct(trip: RoundTrip, entry: float, exit_price: float) -> float:
    return float(trip.net_margin_pct(f"{entry:.10f}", f"{exit_price:.10f}"))


def to_frame(entries: tuple[JournalEntry, ...]) -> pd.DataFrame:
    """Günlüğü CSV dışa aktarımı için tabloya çevirir (SPEC §4.8)."""
    return pd.DataFrame([vars(item) for item in entries])


__all__ = [
    "DEFAULT_FILENAME",
    "STATUS_HIT_STOP",
    "STATUS_HIT_TARGET",
    "STATUS_LABELS_TR",
    "STATUS_OPEN",
    "STATUS_TIMEOUT",
    "JournalEntry",
    "SignalJournal",
    "to_frame",
]
