from datetime import date

from finfly.core.utils import (
    bold,
    escape_md,
    format_amounts,
    format_money,
    normalize_merchant,
    parse_date,
    progress_bar,
)


def test_escape_md():
    assert escape_md("GREEN_FIELD *1 [x]") == r"GREEN\_FIELD \*1 \[x]"


def test_bold_removes_asterisks():
    assert bold("A*B_C") == "*AB_C*"


def test_normalize_merchant():
    assert normalize_merchant("  Green-Field, Inc. ") == "green field inc"
    assert normalize_merchant("ПЯТЁРОЧКА №123") == "пятёрочка 123"


def test_format_money():
    assert format_money(1234.5, "RUB") == "1 234.50 ₽"
    assert format_money(3, "XYZ") == "3.00 XYZ"


def test_format_amounts():
    assert format_amounts({"RUB": 100, "USD": 500}) == "500.00 $ + 100.00 ₽"
    assert format_amounts({}) == "0.00 ₽"


def test_progress_bar():
    assert progress_bar(0.5) == "▓▓▓▓▓░░░░░"
    assert progress_bar(2) == "▓" * 10


def test_parse_date():
    assert parse_date("2016-05-26") == date(2016, 5, 26)
    assert parse_date("") is None
    assert parse_date("26/05/2016") is None
