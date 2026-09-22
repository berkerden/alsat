"""``exchangeInfo`` yanıtının diskte saklanması.

SPEC.md §11: "Komisyon oranlarını, sembol filtrelerini veya limitleri koda
sabit yazma; borsadan çek." Öneri motoru fiyatı ``tickSize``'a, miktarı
``stepSize``'a yuvarlamak ve ``NOTIONAL`` minimumunu kontrol etmek zorunda
(SPEC §4.4), ama arayüz internete çıkmadan da açılabilmeli. Çözüm: filtreler
veri tazeleme adımında bir kez indirilip buraya yazılır, arayüz diskten okur.

Önbellek yoksa **uydurulmuş bir varsayılan kullanılmaz.** Öneri motoru
"filtreler elimde yok" der ve yuvarlanmamış fiyat gösterdiğini açıkça yazar.
Sessizce 0,01'lik bir tickSize varsaymak, borsanın reddedeceği bir emri
geçerli göstermek olurdu.

Ham yanıt olduğu gibi saklanır: ``SymbolRules`` ileride yeni bir alan
okumaya başladığında dosyayı yeniden indirmek gerekmesin diye.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from albsat.core.filters import SymbolRules, parse_exchange_info

DEFAULT_FILENAME = "exchangeinfo.json"


@dataclass(frozen=True)
class ExchangeInfoSnapshot:
    """Diskteki ``exchangeInfo`` kopyası ve ne zaman alındığı."""

    indirilme_zamani_utc: str
    kurallar: Mapping[str, SymbolRules]
    ham: Mapping[str, Any]

    @property
    def semboller(self) -> tuple[str, ...]:
        return tuple(sorted(self.kurallar))

    def rules_for(self, symbol: str) -> SymbolRules | None:
        return self.kurallar.get(symbol.upper())

    def age_days(self, now: datetime | None = None) -> float | None:
        """Kopyanın kaç gün önce alındığı; zaman okunamazsa ``None``."""
        try:
            taken = datetime.fromisoformat(self.indirilme_zamani_utc)
        except ValueError:
            return None
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=UTC)
        now = now or datetime.now(UTC)
        return (now - taken).total_seconds() / 86400.0


class ExchangeInfoStore:
    """``<kök>/exchangeinfo.json`` dosyasını okur ve yazar."""

    def __init__(self, root: Path | str, filename: str = DEFAULT_FILENAME) -> None:
        self.path = Path(root) / filename

    def exists(self) -> bool:
        return self.path.exists()

    def write(self, payload: Mapping[str, Any]) -> Path:
        """Ham ``exchangeInfo`` yanıtını, indirme zamanıyla birlikte yazar."""
        document = {
            "indirilme_zamani_utc": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat(),
            "exchangeInfo": dict(payload),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self.path)
        return self.path

    def read(self) -> ExchangeInfoSnapshot | None:
        """Kopyayı okur; dosya yoksa veya bozuksa ``None`` döner.

        Bozuk dosyada istisna yükseltilmez: arayüzün açılmasını engellemek
        yerine "filtreler elimde yok" demek doğru davranış. Kullanıcı veri
        tazeleme adımını tekrar çalıştırarak düzeltir.
        """
        if not self.path.exists():
            return None
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(document, Mapping):
            return None
        payload = document.get("exchangeInfo")
        if not isinstance(payload, Mapping):
            return None
        try:
            rules = parse_exchange_info(payload)
        except (KeyError, TypeError, ValueError):
            return None
        return ExchangeInfoSnapshot(
            indirilme_zamani_utc=str(document.get("indirilme_zamani_utc", "")),
            kurallar=rules,
            ham=payload,
        )


def refresh(
    store: ExchangeInfoStore,
    http: Any,
    symbols: Sequence[str],
) -> ExchangeInfoSnapshot | None:
    """Filtreleri borsadan çekip önbelleğe yazar.

    ``http`` ``exchange_info`` yöntemi olan herhangi bir nesnedir
    (``albsat.exchange.http.PublicHttp``). Ağ hatası çağırana bırakılır;
    bu fonksiyonun sessizce başarısız olması, kullanıcının eski filtrelerle
    çalıştığını fark etmemesine yol açardı.
    """
    payload = http.exchange_info(list(symbols))
    store.write(payload)
    return store.read()


__all__ = [
    "DEFAULT_FILENAME",
    "ExchangeInfoSnapshot",
    "ExchangeInfoStore",
    "refresh",
]
