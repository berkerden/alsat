"""Emir yürütme ayarları (``config/default.yaml`` → ``emir``).

Arayüzden değiştirilebilir, değişiklik ``demo_durum`` tablosunda saklanır ve
denetim kaydına yazılır. Buradaki varsayılanlar ``config/default.yaml`` ile
aynıdır; ``tests/test_demo_yurutme.py`` ikisinin ayrışmadığını denetler.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

STOP_MARKET = "STOP_LOSS"
STOP_LIMIT = "STOP_LOSS_LIMIT"

STOP_TYPES_TR = {
    STOP_MARKET: "Piyasa stop (STOP_LOSS): tetiklenince her koşulda satar; boşluklu "
                 "düşüşte stopun altında dolabilir. Taker komisyonu.",
    STOP_LIMIT: "Limitli stop (STOP_LOSS_LIMIT): tetiklenince stopun belirli bir yüzde "
                "altına limit satış koyar; fiyat o limitin altına hızla inerse DOLMAYABİLİR "
                "ve pozisyon korumasız kalır.",
}


class SettingsError(ValueError):
    """Geçersiz ayar; mesaj kullanıcıya gösterilir."""


@dataclass(frozen=True)
class ExecutionSettings:
    stop_tipi: str = STOP_MARKET
    #: Limitli stopta limit fiyatının stopun ne kadar altında olacağı (yüzde).
    stop_limit_ofset_yuzde: Decimal = Decimal("0.5")
    #: Korumalı çıkışta (IOC) en iyi alışın en fazla ne kadar altına satılacağı (yüzde).
    azami_kayma_yuzde: Decimal = Decimal("0.5")
    #: Kısmi dolumda pozisyonun korumasız kalabileceği en uzun süre.
    korumasiz_azami_saniye: int = 20
    #: Limit-maker giriş reddedilirse kural emrinin kaç kez yeniden fiyatlanacağı.
    yeniden_fiyatlama_denemesi: int = 3

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in data.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ExecutionSettings:
        base = cls()
        if not data:
            return base
        try:
            return base.updated(data)
        except SettingsError:
            return base

    def updated(self, changes: dict[str, Any]) -> ExecutionSettings:
        values: dict[str, Any] = {}
        for key, value in changes.items():
            if key == "stop_tipi":
                if value not in (STOP_MARKET, STOP_LIMIT):
                    raise SettingsError("Stop tipi STOP_LOSS ya da STOP_LOSS_LIMIT olmalı.")
                values[key] = value
            elif key in ("stop_limit_ofset_yuzde", "azami_kayma_yuzde"):
                try:
                    number = Decimal(str(value))
                except (InvalidOperation, ValueError):
                    raise SettingsError(f"{key} sayı olmalı.") from None
                if not Decimal("0.05") <= number <= Decimal("5"):
                    raise SettingsError(f"{key} %0.05 ile %5 arasında olmalı.")
                values[key] = number
            elif key == "korumasiz_azami_saniye":
                number_int = _int(key, value)
                if not 5 <= number_int <= 300:
                    raise SettingsError("Korumasız kalma süresi 5 ile 300 saniye arasında olmalı.")
                values[key] = number_int
            elif key == "yeniden_fiyatlama_denemesi":
                number_int = _int(key, value)
                if not 0 <= number_int <= 5:
                    raise SettingsError("Yeniden fiyatlama denemesi 0 ile 5 arasında olmalı.")
                values[key] = number_int
            else:
                raise SettingsError(f"Bilinmeyen ayar: {key}")
        return replace(self, **values)


def _int(key: str, value: Any) -> int:
    if isinstance(value, bool):
        raise SettingsError(f"{key} tam sayı olmalı.")
    try:
        return int(str(value))
    except ValueError:
        raise SettingsError(f"{key} tam sayı olmalı.") from None


__all__ = [
    "STOP_LIMIT",
    "STOP_MARKET",
    "STOP_TYPES_TR",
    "ExecutionSettings",
    "SettingsError",
]
