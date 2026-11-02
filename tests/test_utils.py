from datetime import date

import pytest

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


def test_bold_strips_inner_asterisks():
    # Общий бот использует legacy Markdown, а не MarkdownV2.
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


def test_format_money_zero_and_negative():
    assert format_money(0, "RUB") == "0.00 ₽"
    assert format_money(-1234.5, "RUB") == "-1 234.50 ₽"


def test_normalize_merchant_empty():
    assert normalize_merchant("") == ""
    assert normalize_merchant("   ") == ""
    assert normalize_merchant("---, !!!") == ""


def test_parse_date_invalid_calendar_dates():
    assert parse_date("2026-02-30") is None
    assert parse_date("2026-02-29") is None
    assert parse_date("2026-13-01") is None
    assert parse_date("2028-02-29") == date(2028, 2, 29)


def test_progress_bar_boundaries():
    assert progress_bar(-0.5) == "░" * 10
    assert progress_bar(0) == "░" * 10
    assert progress_bar(1) == "▓" * 10
    assert progress_bar(0.5, width=4) == "▓▓░░"


def test_empty_text_formatting():
    assert escape_md("") == ""
    # Сохраняем существующий контракт общей утилиты команды.
    assert bold("") == "**"


def test_format_amounts_zero_currency():
    assert format_amounts({"USD": 0}) == "0.00 $"


def test_large_amount_and_rounding():
    assert format_money(1234567890.25, "RUB") == "1 234 567 890.25 ₽"
    assert format_money(12.346, "EUR") == "12.35 €"


def test_unicode_and_markdown_in_shop():
    assert escape_md("🛒 A_B [shop]") == "🛒 A\\_B \\[shop]"
    assert normalize_merchant(" ПЯТЁРОЧКА!!! ") == "пятёрочка"
    assert normalize_merchant("Green-Field") == normalize_merchant(
        "green field"
    )


def test_date_with_surrounding_spaces():
    assert parse_date(" 2026-10-01 ") == date(2026, 10, 1)


@pytest.mark.parametrize(
    "value,expected",
    [("\\", "\\"), (".-()!#+=|{}", ".-()!#+=|{}"), ("`", "\\`"), ("", "")],
)
def test_escape_md_legacy_contract(value, expected):
    assert escape_md(value) == expected


def test_equal_amounts_keep_insertion_order():
    assert format_amounts({"USD": 100, "EUR": 100}) == "100.00 $ + 100.00 €"
    assert format_amounts({"EUR": 100, "USD": 100}) == "100.00 € + 100.00 $"


def test_amounts_negative_and_three_currencies():
    assert (
        format_amounts({"USD": -100, "RUB": 50, "EUR": 20})
        == "50.00 ₽ + 20.00 € + -100.00 $"
    )


@pytest.mark.parametrize(
    "value,width,expected",
    [
        (0.5, 1, "░"),
        (0.999, 10, "▓" * 10),
        (0.5, 0, ""),
        (0, 1, "░"),
        (1, 1, "▓"),
    ],
)
def test_progress_width_and_rounding(value, width, expected):
    # round(0.5) == 0 в существующей общей утилите.
    assert progress_bar(value, width=width) == expected


@pytest.mark.parametrize(
    "value,expected", [("№", ""), ("123", "123"), ("ЁЖИК", "ёжик")]
)
def test_merchant_numbers_and_yo(value, expected):
    assert normalize_merchant(value) == expected


def test_date_requires_zero_padded_iso():
    assert parse_date("2026-5-26") is None


def test_none_amount_is_outside_utility_contract():
    with pytest.raises((TypeError, ValueError)):
        format_money(None, "RUB")
