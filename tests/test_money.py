"""Yuvarlama testleri: emir fiyat/miktarı buradan geçiyor, hata affetmez."""
from decimal import Decimal

import pytest

from albsat.core.money import (
    Rounding,
    format_for_api,
    increment_decimals,
    is_multiple_of,
    round_to_increment,
    to_decimal,
)


def test_float_reddedilir():
    # Decimal(0.1) bozuk bir değerdir; sessizce kabul edilmemeli.
    with pytest.raises(TypeError):
        to_decimal(0.1)


def test_bool_reddedilir():
    with pytest.raises(TypeError):
        to_decimal(True)


@pytest.mark.parametrize(
    "value,increment,mode,expected",
    [
        ("0.123456", "0.01", Rounding.FLOOR, "0.12"),
        ("0.123456", "0.01", Rounding.CEILING, "0.13"),
        ("0.125", "0.01", Rounding.NEAREST, "0.13"),
        ("43210.567", "0.01", Rounding.FLOOR, "43210.56"),
        ("100", "0", Rounding.FLOOR, "100"),  # adım yoksa değer korunur
    ],
)
def test_round_to_increment(value, increment, mode, expected):
    assert round_to_increment(value, increment, mode) == Decimal(expected)


def test_base_offsetli_yuvarlama():
    # PRICE_FILTER kuralı: (price - minPrice) % tickSize == 0
    result = round_to_increment("10.007", "0.01", Rounding.FLOOR, base="0.005")
    assert result == Decimal("10.005")
    assert is_multiple_of(result, "0.01", base="0.005")


def test_yuvarlama_sonucu_daima_adim_katidir():
    for raw in ("0.1", "1.05", "99.999", "43210.5678"):
        result = round_to_increment(raw, "0.01", Rounding.FLOOR)
        assert is_multiple_of(result, "0.01")


def test_format_for_api_bilimsel_gosterim_uretmez():
    assert format_for_api(Decimal("1E-8")) == "0.00000001"
    assert format_for_api(Decimal("0.10000")) == "0.1"
    assert format_for_api(Decimal("1E+3")) == "1000"


def test_increment_decimals():
    assert increment_decimals("0.00100000") == 3
    assert increment_decimals("1") == 0
