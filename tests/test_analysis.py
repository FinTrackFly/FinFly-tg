import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from finfly.services import stats as s


def tx(
    i=1,
    *,
    amount=100,
    currency="RUB",
    day=date(2026, 10, 1),
    user=1,
    merchant="Магазин",
    card=1,
    transfer=False,
    category="Продукты",
    added=None,
):
    return SimpleNamespace(
        id=i,
        user_id=user,
        amount=amount,
        currency=currency,
        purchase_date=day,
        created_at=added or datetime(2026, 11, 1),
        merchant=merchant,
        card_id=card,
        is_transfer=transfer,
        category=category,
        description="",
        category_edited=False,
        processing_time=1,
    )


def test_decimal_totals_currency_average_and_transfers():
    summary = s.build_summary(
        [
            tx(amount=0.1),
            tx(2, amount=0.2),
            tx(3, amount=10, currency="USD"),
            tx(4, amount=999, transfer=True),
        ]
    )
    assert summary.totals == {"RUB": Decimal("0.3"), "USD": 10}
    assert summary.count == 3 and summary.transfers == 1
    rendered = s.format_summary(summary, [], "тест")
    assert "0.15 ₽" in rendered and "10.00 $" in rendered
    assert "100%" in rendered


def test_only_transfers_still_visible():
    text = s.format_summary(s.build_summary([tx(transfer=True)]), [], "тест")
    assert (
        "Расходов за этот период нет" in text
        and "исключённых из расходов: 1" in text
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad", None])
def test_invalid_money_not_propagated(value):
    assert s.money(value) == 0


@pytest.mark.parametrize(
    "preset,start,end",
    [
        ("today", date(2026, 11, 1), date(2026, 11, 2)),
        ("yesterday", date(2026, 10, 31), date(2026, 11, 1)),
        ("week", date(2026, 10, 26), date(2026, 11, 2)),
        ("month", date(2026, 11, 1), date(2026, 11, 2)),
        ("7", date(2026, 10, 26), date(2026, 11, 2)),
        ("30", date(2026, 10, 3), date(2026, 11, 2)),
    ],
)
def test_periods(preset, start, end):
    assert s.period_bounds({"period": preset}, date(2026, 11, 1)) == (
        start,
        end,
    )


def test_purchase_date_late_upload_and_previous_boundary():
    view = {"period": "custom", "start": "2026-10-01", "end": "2026-10-31"}
    rows = [
        tx(1, day=date(2026, 10, 1)),
        tx(2, day=date(2026, 10, 31)),
        tx(3, day=date(2026, 9, 30)),
        tx(4, day=date(2026, 11, 1)),
    ]
    assert [t.id for t in s.select_transactions(rows, view, user_id=1)] == [
        1,
        2,
    ]
    assert [
        t.id
        for t in s.select_transactions(rows, view, user_id=1, previous=True)
    ] == [3]


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-11-02", "2026-11-03"),
        ("2026-11-01", "2026-10-01"),
        ("2026-02-30", "2026-03-01"),
    ],
)
def test_invalid_dates(start, end):
    with pytest.raises(ValueError):
        s.period_bounds(
            {"period": "custom", "start": start, "end": end}, date(2026, 11, 1)
        )


def test_filters_and_ownership():
    rows = [
        tx(1),
        tx(2, user=2),
        tx(3, currency="USD"),
        tx(4, card=None),
        tx(5, merchant="Другое"),
    ]
    view = {
        "period": "all",
        "currency": "RUB",
        "card_id": 1,
        "merchant": "магаз",
        "category": "Продукты",
    }
    assert [t.id for t in s.select_transactions(rows, view, user_id=1)] == [1]


def test_comparison_has_no_percent_from_zero():
    before = s.build_summary([])
    after = s.build_summary([tx()])
    assert s.comparison_rows(after, before)[0]["percent"] is None
    assert "нет базы" in s.format_comparison(after, before)


def test_series_week_and_forecast_leap_year():
    rows = [
        tx(1, amount=100, day=date(2024, 2, 1)),
        tx(2, amount=200, day=date(2024, 2, 2)),
        tx(3, amount=999, transfer=True, day=date(2024, 2, 2)),
    ]
    assert s.time_series(rows, weekly=True)["RUB"][date(2024, 1, 29)] == 300
    assert s.forecast_month(rows, date(2024, 2, 5))["RUB"] == 1740


def subscription_rows(days, amount=500):
    return [
        tx(
            i + 1,
            amount=amount,
            day=day,
            merchant="Video Service",
            category="Связь и подписки",
        )
        for i, day in enumerate(days)
    ]


def test_month_end_subscription_and_trial():
    rows = subscription_rows(
        [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    )
    rows.append(
        tx(99, amount=1, day=date(2026, 1, 20), merchant="Video Service")
    )
    subscriptions = s.detect_subscriptions(rows, date(2026, 4, 1))
    assert len(subscriptions) == 1
    assert subscriptions[0]["next"] == "2026-04-30"
    assert subscriptions[0]["trial"]


@pytest.mark.parametrize(
    "days",
    [
        [date(2026, 10, 1)],
        [date(2026, 10, 1)] * 3,
        [date(2026, 9, 1), date(2026, 9, 15), date(2026, 10, 3)],
    ],
)
def test_not_every_repeat_is_subscription(days):
    assert not s.detect_subscriptions(
        subscription_rows(days), date(2026, 11, 1)
    )


def test_subscriptions_do_not_mix_cards_currency_or_old_data():
    rows = [
        tx(1, day=date(2026, 9, 1)),
        tx(2, day=date(2026, 10, 1), card=2),
        tx(3, day=date(2026, 11, 1), currency="USD"),
    ]
    assert not s.detect_subscriptions(rows, date(2026, 11, 1))
    assert not s.detect_subscriptions(
        subscription_rows([date(2025, 1, 1), date(2025, 2, 1)]),
        date(2026, 11, 1),
    )


def test_weekly_and_annual_subscriptions():
    weekly = subscription_rows(
        [date(2026, 10, 17), date(2026, 10, 24), date(2026, 10, 31)]
    )
    assert (
        s.detect_subscriptions(weekly, date(2026, 11, 1))[0]["cadence"]
        == "еженедельно"
    )
    annual = subscription_rows([date(2025, 10, 31), date(2026, 10, 31)])
    assert (
        s.detect_subscriptions(annual, date(2026, 11, 1))[0]["cadence"]
        == "ежегодно"
    )


def test_subscription_identity_survives_price_change():
    assert s.subscription_key("Video-Service", "RUB", 1) == s.subscription_key(
        "video service", "RUB", 1
    )


def baseline():
    return [
        tx(i, day=date(2026, 10, 1) + timedelta(days=i * 3))
        for i in range(1, 7)
    ]


def test_unusual_game_and_large_amount_and_acknowledgement():
    rows = baseline() + [
        tx(99, amount=700, day=date(2026, 10, 30), merchant="ROBLOX")
    ]
    alerts = s.detect_anomalies(rows, date(2026, 11, 1))
    assert len(alerts) == 1 and len(alerts[0]["reasons"]) == 2
    assert not s.detect_anomalies(rows, date(2026, 11, 1), acknowledged=[99])


def test_regular_gamer_not_flagged_and_sparse_history_not_judged():
    rows = baseline()
    rows[0].merchant = "Steam"
    rows.append(tx(99, day=date(2026, 10, 30), merchant="ROBLOX"))
    assert not s.detect_anomalies(rows, date(2026, 11, 1))
    assert not s.detect_anomalies(rows[-1:], date(2026, 11, 1))


def test_cashback_caps_shared_across_categories_and_month_reset():
    cards = [SimpleNamespace(id=1, name="A"), SimpleNamespace(id=2, name="B")]
    rules = [
        dict(card_id=1, currency="RUB", category="*", rate="10", cap="15")
    ]
    rows = [
        tx(1, amount=100, day=date(2026, 10, 1)),
        tx(2, amount=100, day=date(2026, 10, 2), category="Транспорт"),
        tx(3, amount=100, day=date(2026, 11, 1)),
    ]
    estimate = s.cashback_estimate(rows, cards, rules)
    assert estimate["planned"]["RUB"] == 25
    assert estimate["actual"]["RUB"] == 25


@pytest.mark.parametrize(
    "line",
    [
        "1; RUB; *; 101; нет",
        "1; RUB; *; NaN; нет",
        "2; RUB; *; 5; 100",
        "1; RU; *; 5; 100",
    ],
)
def test_invalid_cashback(line):
    with pytest.raises(ValueError):
        s.parse_cashback_rule(line, [SimpleNamespace(id=1)])


def test_xlsx_and_csv_formula_injection():
    rows = [tx(merchant='=HYPERLINK("bad")', amount=12.5)]
    csv_rows = list(
        csv.reader(
            io.StringIO(s.csv_bytes(rows, []).decode("utf-8-sig")),
            delimiter=";",
        )
    )
    assert csv_rows[1][2].startswith("'=")
    with ZipFile(io.BytesIO(s.xlsx_bytes(rows, []))) as archive:
        xml = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert not xml.findall(".//{*}f")
        assert xml.find(".//{*}c[@r='E2']/{*}v").text == "12.50"


def test_svg_escapes_shop_and_currency():
    chart = s.chart_svg({"<script>": Decimal(5)}, "RUB", "Расходы")
    ET.fromstring(chart)
    assert b"<script>" not in chart


def test_long_text_respects_utf16_and_roundtrip():
    text = "😀" * 4000 + " привет " * 2000
    chunks = s.split_text(text)
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 3000 for chunk in chunks)


@pytest.mark.asyncio
async def test_options_are_persistent_and_user_scoped(database):
    await s.set_option(1, "subscription:key", "important")
    await s.set_option(2, "subscription:key", "review")
    await s.set_option(1, "goal", "subscriptions")
    assert (await s.get_options(1))["subscription:key"] == "important"
    assert (await s.get_options(2))["subscription:key"] == "review"
    assert "goal" not in await s.get_options(2)


@pytest.mark.parametrize(
    "offset,hour,expected",
    [
        ("+03:00", 22, date(2026, 11, 2)),
        ("+12:00", 12, date(2026, 11, 2)),
        ("-05:00", 2, date(2026, 10, 31)),
    ],
)
def test_timezone_midnight_naive_and_aware(offset, hour, expected):
    from datetime import UTC

    tz = s.user_timezone(offset)
    now = datetime(2026, 11, 1, hour)
    for instant in (now, now.replace(tzinfo=UTC)):
        assert s.local_today(tz, instant) == expected
        assert s.transaction_date(tx(day=None, added=instant), tz) == expected
    assert s.transaction_date(
        tx(day=date(2026, 10, 12), added=now), tz
    ) == date(2026, 10, 12)


@pytest.mark.parametrize(
    "offset", ["", "abc", "+14:01", "-12:01", "+25", "+03:99", None]
)
def test_invalid_timezone(offset):
    with pytest.raises(ValueError, match="часовой пояс|Часовой пояс"):
        s.user_timezone(offset)


def test_period_uses_user_today_at_midnight(monkeypatch):
    monkeypatch.setattr(s, "utc_now", lambda: datetime(2026, 11, 1, 23, 30))
    view = {"period": "today", "timezone": "+03:00"}
    assert s.period_bounds(view) == (date(2026, 11, 2), date(2026, 11, 3))
    assert [
        t.id
        for t in s.select_transactions(
            [tx(day=None, added=datetime(2026, 11, 1, 22))], view, user_id=1
        )
    ] == [1]


@pytest.mark.parametrize(
    "preset,start,end",
    [
        ("previous_month", date(2026, 10, 1), date(2026, 11, 1)),
        ("year", date(2026, 1, 1), date(2026, 11, 2)),
        ("90", date(2026, 8, 4), date(2026, 11, 2)),
        ("all", None, date(2026, 11, 2)),
    ],
)
def test_extended_periods(preset, start, end):
    assert s.period_bounds({"period": preset}, date(2026, 11, 1)) == (
        start,
        end,
    )


@pytest.mark.parametrize(
    "view",
    [
        {"period": "custom"},
        {"period": "custom", "start": "x", "end": "x"},
        {"period": "custom", "start": None, "end": []},
    ],
)
def test_custom_dates_always_have_friendly_error(view):
    with pytest.raises(ValueError, match="Укажи две даты"):
        s.period_bounds(view)


def test_refunds_net_totals_but_purchase_mean_is_gross():
    flagged = tx(3, amount=20, category="Транспорт")
    flagged.is_refund = True
    summary = s.build_summary(
        [
            tx(amount=100),
            tx(2, amount=-40),
            flagged,
            tx(4, amount=-999, transfer=True),
        ]
    )
    assert summary.totals == {"RUB": 40}
    assert summary.refunded == {"RUB": 60}
    assert summary.count == 1 and summary.refunds == 2
    rendered = s.format_summary(summary, [], "период")
    assert "Средний чек покупок: 100.00 ₽" in rendered
    assert "Возвращено: 60.00 ₽" in rendered
    assert "%" not in rendered
    only_refunds = s.format_summary(
        s.build_summary([tx(amount=-50)]), [], "период"
    )
    assert "-50.00 ₽" in only_refunds and "Средний чек" not in only_refunds


def test_refunds_in_series_exports_and_forecast():
    rows = [
        tx(amount=100, day=date(2026, 10, 5)),
        tx(2, amount=-40, day=date(2026, 10, 5)),
    ]
    assert s.time_series(rows)["RUB"][date(2026, 10, 5)] == 60
    assert s.forecast_month(rows, date(2026, 10, 5))["RUB"] == Decimal(
        "372.00"
    )
    data = list(
        csv.reader(
            io.StringIO(s.csv_bytes(rows, []).decode("utf-8-sig")),
            delimiter=";",
        )
    )
    assert data[2][4] == "-40.00"


def test_forecast_waits_until_day_five():
    with pytest.raises(ValueError, match="с 5-го"):
        s.forecast_month([tx()], date(2026, 10, 4))


@pytest.mark.parametrize(
    "day,step,expected",
    [
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2025, 1, 31), 1, date(2025, 2, 28)),
        (date(2026, 3, 31), 1, date(2026, 4, 30)),
        (date(2026, 12, 31), 1, date(2027, 1, 31)),
        (date(2026, 1, 31), -1, date(2025, 12, 31)),
    ],
)
def test_add_month_calendar(day, step, expected):
    assert s.add_month(day, step) == expected


@pytest.mark.parametrize(
    "step,cadence", [(3, "ежеквартально"), (6, "раз в полгода")]
)
def test_longer_subscription_intervals(step, cadence):
    days = [date(2026, 10 - step, 30), date(2026, 10, 30)]
    assert (
        s.detect_subscriptions(subscription_rows(days), date(2026, 11, 1))[0][
            "cadence"
        ]
        == cadence
    )


def test_two_weekly_payments_insufficient_and_new_price_series():
    assert not s.detect_subscriptions(
        subscription_rows([date(2026, 10, 24), date(2026, 10, 31)]),
        date(2026, 11, 1),
    )
    rows = subscription_rows(
        [date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31)]
    )
    key = s.detect_subscriptions(rows, date(2026, 11, 1))[0]["key"]
    rows[1].amount = rows[2].amount = 700
    result = s.detect_subscriptions(rows, date(2026, 11, 1))[0]
    assert result["key"] == key and Decimal(result["amount"]) == 700


def test_rolling_anomaly_matches_naive_reference():
    import random
    import statistics

    rng = random.Random(812)
    today = date(2026, 11, 1)
    rows = [
        tx(
            i,
            amount=rng.choice([10, 20, 30, 150, 300]),
            day=today - timedelta(days=250 - i // 4),
            card=rng.choice([1, 2]),
            currency=rng.choice(["RUB", "USD"]),
        )
        for i in range(1000)
    ]
    expected = []
    for index, item in enumerate(rows):
        prior = [
            old
            for old in rows[:index]
            if old.card_id == item.card_id
            and old.currency == item.currency
            and (item.purchase_date - old.purchase_date).days <= 180
        ]
        if (
            len(prior) >= 5
            and (item.purchase_date - prior[0].purchase_date).days >= 14
            and (today - item.purchase_date).days <= 30
        ):
            if (
                item.amount
                >= statistics.median([old.amount for old in prior]) * 5
            ):
                expected.append(item.id)
    assert [alert["id"] for alert in s.detect_anomalies(rows, today)] == list(
        reversed(expected)
    )


def test_anomalies_ten_thousand_rows_and_expired_history():
    today = date(2026, 11, 1)
    rows = [
        tx(i, day=today - timedelta(days=200 - i // 50)) for i in range(10000)
    ]
    rows.append(tx(10001, day=today, amount=1000))
    assert s.detect_anomalies(rows, today)[0]["id"] == 10001
    old = [tx(i, day=today - timedelta(days=200 + i)) for i in range(20)]
    assert not s.detect_anomalies(
        old + [tx(10001, day=today, amount=1000)], today
    )


@pytest.mark.parametrize(
    "line,fragment",
    [
        ("abc; RUB; *; 5; нет", "ID карты"),
        ("1; RUB; *; пять; нет", "числами"),
        (";", "Формат"),
        ("1; RUB; *; 5; Infinity", "неотрицательным"),
    ],
)
def test_cashback_friendly_parsing(line, fragment):
    with pytest.raises(ValueError, match=fragment):
        s.parse_cashback_rule(line, [SimpleNamespace(id=1)])


def test_cashback_duplicate_rules_and_refunds_are_explicit():
    cards = [SimpleNamespace(id=1, name="A")]
    rule = dict(card_id=1, currency="RUB", category="*", rate="5", cap=None)
    with pytest.raises(ValueError, match="повторные"):
        s.cashback_estimate([tx()], cards, [rule, rule])
    with pytest.raises(ValueError, match="возвраты"):
        s.cashback_estimate([tx(amount=-50)], cards, [rule])


def test_xlsx_opens_header_frozen_and_columns_beyond_z():
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(s.xlsx_bytes([tx(amount=-50)], [])))
    assert workbook.active["E2"].value == -50
    assert workbook.active.freeze_panes == "A2"
    assert [s.col_letter(i) for i in [1, 26, 27, 52, 53, 703]] == [
        "A",
        "Z",
        "AA",
        "AZ",
        "BA",
        "AAA",
    ]


def test_svg_accessibility_and_refund_bar():
    chart = s.chart_svg(
        {"Название" * 10: 100, "Возврат": -40}, "RUB", "Расходы"
    )
    root = ET.fromstring(chart)
    assert (
        root.find("{*}title") is not None and root.find("{*}desc") is not None
    )
    assert "…" in chart.decode() and "-40.00" in chart.decode()
    assert "%" not in chart.decode().replace('width="100%" height="100%"', "")


def test_legacy_metrics_and_unchanged_comparison():
    item = tx()
    del item.category_edited, item.processing_time
    assert "0.00 с" in s.format_metrics([item])
    summary = s.build_summary([item])
    assert "Изменений по категориям нет" in s.format_comparison(
        summary, summary
    )


@pytest.mark.asyncio
async def test_corrupt_settings_skipped_and_view_not_mutated(database):
    from sqlalchemy import text
    from finfly.db.engine import engine

    await s.set_option(1, "timezone", "garbage")
    await s.set_option(1, "protected_categories", [None])
    await s.set_option(1, "cashback_rules", ["bad"])
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO analysis_settings VALUES (1, 'goal', '{bad')")
        )
    assert await s.get_options(1) == {}
    options = {"stats_view": {"period": "7"}}
    view = s.read_view(options)
    view["period"] = "30"
    assert options["stats_view"]["period"] == "7"


@pytest.mark.asyncio
async def test_sql_purchase_dates_and_utc_boundary_and_owner(database):
    from finfly.db.models import Transaction

    values = [
        (1, 1, date(2026, 11, 1), datetime(2026, 12, 1)),
        (2, 1, date(2026, 10, 1), datetime(2026, 11, 1)),
        (3, 1, None, datetime(2026, 10, 31, 22)),
        (4, 1, None, datetime(2026, 11, 1, 21)),
        (5, 2, date(2026, 11, 1), datetime(2026, 11, 1)),
    ]
    async with database() as session:
        session.add_all(
            [
                Transaction(
                    id=i,
                    user_id=uid,
                    purchase_date=day,
                    created_at=added,
                    amount=100,
                    currency="RUB",
                    category="Продукты",
                    merchant="Shop",
                )
                for i, uid, day, added in values
            ]
        )
        await session.commit()
    result = await s.load_transactions(
        1,
        start=date(2026, 11, 1),
        end=date(2026, 11, 2),
        tz=s.user_timezone("+03:00"),
    )
    assert {item.id for item in result} == {1, 3}


def test_rate_limit_scoped_and_expires():
    assert s.allow_action("llm", 1, interval=15, clock=0)
    assert not s.allow_action("llm", 1, interval=15, clock=14)
    assert s.allow_action("llm", 2, interval=15, clock=14)
    assert s.allow_action("analysis", 1, interval=0.25, clock=14)
    assert s.allow_action("llm", 1, interval=15, clock=15)
