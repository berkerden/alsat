"""Parasal değerler ve borsa artış adımlarına (tick/step) yuvarlama.

Kural: Bu projede fiyat, miktar ve bakiye **her zaman** ``Decimal``. ``float``
kullanılmaz. Bir değerin ``float`` olarak buraya girmesi ``TypeError`` ile
reddedilir; sessizce ``Decimal``'e çevrilmez, çünkü ``Decimal(0.1)`` zaten
bozuk bir değerdir.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from enum import Enum
from typing import Union

Number = Union[Decimal, int, str]

ZERO = Decimal("0")
ONE = Decimal("1")
ONE_HUNDRED = Decimal("100")


class Rounding(str, Enum):
    """Yuvarlama yönü."""

    FLOOR = "floor"
    CEILING = "ceiling"
    NEAREST = "nearest"


def to_decimal(value: Number) -> Decimal:
    """``Decimal``'e çevirir; ``float`` kabul etmez.

    ``float`` reddedilir çünkü ``Decimal(0.1)`` == 0.1000000000000000055...
    olur ve bu hata emir fiyatlarına kadar sızar.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool, int'in alt sınıfı — kazara geçmesin
        raise TypeError("Parasal değer olarak bool kullanılamaz")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    if isinstance(value, float):
        raise TypeError(
            "Parasal değerlerde float kullanılamaz; str veya Decimal verin "
            f"(gelen: {value!r})"
        )
    raise TypeError(f"Decimal'e çevrilemeyen tür: {type(value).__name__}")


def round_to_increment(
    value: Number,
    increment: Number,
    mode: Rounding = Rounding.FLOOR,
    base: Number = ZERO,
) -> Decimal:
    """``value``'yu ``base + n * increment`` biçimine yuvarlar.

    Binance'in ``PRICE_FILTER`` kuralı ``(price - minPrice) % tickSize == 0``
    olduğu için ``base`` parametresi vardır; ``LOT_SIZE`` için ``base=minQty``
    kullanılır.

    ``increment`` sıfır veya negatifse değer olduğu gibi döner (Binance bazı
    sembollerde ``tickSize: "0"`` göndererek "adım kısıtı yok" der).
    """
    value = to_decimal(value)
    increment = to_decimal(increment)
    base = to_decimal(base)

    if increment <= ZERO:
        return value

    steps = (value - base) / increment
    if mode is Rounding.FLOOR:
        steps = steps.to_integral_value(rounding=ROUND_FLOOR)
    elif mode is Rounding.CEILING:
        steps = steps.to_integral_value(rounding=ROUND_CEILING)
    elif mode is Rounding.NEAREST:
        steps = steps.to_integral_value(rounding=ROUND_HALF_UP)
    else:  # pragma: no cover - Enum dışı değer gelemez
        raise ValueError(f"Bilinmeyen yuvarlama modu: {mode}")

    return _quantize(base + steps * increment, increment, base)


def _quantize(value: Decimal, increment: Decimal, base: Decimal) -> Decimal:
    """Sonucu, adım **ve** taban ofsetinin gerektirdiği hassasiyete sabitler.

    ``base`` adımın katı değilse (ör. ``minPrice=0.005``, ``tickSize=0.01``)
    geçerli sonuç adımdan daha fazla basamak içerir. Yalnızca adıma göre
    sabitlemek bu değeri bozar ve filtreyi ihlal eden bir fiyat üretir.
    """
    exponent = _exponent(increment)
    if base != ZERO:
        exponent = min(exponent, _exponent(base))
    return value.quantize(Decimal(1).scaleb(exponent))


def _exponent(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    if not isinstance(exponent, int):  # NaN / Infinity
        raise ValueError(f"Geçersiz değer: {value}")
    return exponent


def quantize_to_increment(value: Number, increment: Number) -> Decimal:
    """Değeri, artış adımının ondalık hassasiyetine sabitler.

    Borsaya ``0.1000000000`` yerine ``0.10`` göndermek için; sayısal değeri
    değiştirmez, yalnızca üssü (exponent) düzeltir.
    """
    value = to_decimal(value)
    increment = to_decimal(increment)
    if increment <= ZERO:
        return value
    return value.quantize(increment.normalize())


def format_for_api(value: Number) -> str:
    """Binance'e gönderilecek sayı metnini üretir.

    Bilimsel gösterim (``1E-8``) Binance tarafından reddedildiği için asla
    üretilmez.
    """
    value = to_decimal(value)
    sign, digits, exponent = value.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        # 1E+3 gibi değerleri 1000 olarak düzleştir
        value = value.quantize(Decimal(1))
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def increment_decimals(increment: Number) -> int:
    """Artış adımının kaç ondalık basamak içerdiğini döndürür."""
    increment = to_decimal(increment).normalize()
    exponent = increment.as_tuple().exponent
    if not isinstance(exponent, int):  # NaN / Infinity
        raise ValueError(f"Geçersiz artış adımı: {increment}")
    return max(0, -exponent)


def is_multiple_of(value: Number, increment: Number, base: Number = ZERO) -> bool:
    """``value``, ``base``'ten itibaren ``increment``'in tam katı mı?"""
    value = to_decimal(value)
    increment = to_decimal(increment)
    base = to_decimal(base)
    if increment <= ZERO:
        return True
    remainder = (value - base) % increment
    return remainder == ZERO


def pct(part: Number, whole: Number) -> Decimal:
    """Yüzde hesabı; ``whole`` sıfırsa ``Decimal("0")`` döner."""
    part = to_decimal(part)
    whole = to_decimal(whole)
    if whole == ZERO:
        return ZERO
    return part / whole * Decimal(100)
