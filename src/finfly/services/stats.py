"""Расходы, сигналы для проверки и настройки раздела аналитики.

Даты покупки имеют приоритет над датой загрузки. Денежные суммы разных
валют никогда не складываются. Сигналы не являются доказательством обмана.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import logging
import re
import time
import zipfile
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape

from finfly.core.utils import median_or_zero, normalize_merchant, utc_now

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
CENT = Decimal("0.01")
SYMBOLS = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CNY": "¥",
    "KZT": "₸",
}
PERIODS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "week": "Эта неделя",
    "month": "Этот месяц",
    "previous_month": "Прошлый месяц",
    "year": "Этот год",
    "90": "90 дней",
    "7": "7 дней",
    "30": "30 дней",
    "all": "Всё время",
}
GAME_WORDS = (
    "steam",
    "roblox",
    "fortnite",
    "genshin",
    "tencent games",
    "playstation",
    "xbox",
    "донат",
    "внутриигров",
    "game pass",
)


def money(value) -> Decimal:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else ZERO
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def display_money(value, currency: str) -> str:
    # Розничное округление: ровно половина копейки округляется от нуля.
    rounded = money(value).quantize(CENT, rounding=ROUND_HALF_UP)
    return (
        f"{rounded:,.2f}".replace(",", " ")
        + " "
        + SYMBOLS.get(currency, currency)
    )


def amounts_text(amounts: dict) -> str:
    return (
        "; ".join(display_money(v, c) for c, v in sorted(amounts.items()))
        or "нет расходов"
    )


def stable_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def user_timezone(value="+00:00"):
    if not isinstance(value, str):
        raise ValueError("Укажи часовой пояс, например +03:00 или +05:30.")
    match = re.fullmatch(
        r"(?:UTC)?([+-])(\d{1,2})(?::([0-5]\d))?", value.strip().upper()
    )
    if not match:
        raise ValueError("Укажи часовой пояс, например +03:00 или +05:30.")
    minutes = int(match[2]) * 60 + int(match[3] or 0)
    if match[1] == "-":
        minutes = -minutes
    if not -720 <= minutes <= 840:
        raise ValueError("Часовой пояс должен быть от UTC−12:00 до UTC+14:00.")
    return timezone(timedelta(minutes=minutes))


def local_today(tz=UTC, now=None):
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(tz).date()


def read_view(options):
    saved = options.get("stats_view")
    view = dict(saved) if isinstance(saved, dict) else {"period": "30"}
    view["timezone"] = options.get("timezone", "+00:00")
    try:
        period_bounds(view)
    except ValueError:
        logger.warning("Неверный сохранённый период сброшен")
        view.update(period="30")
        view.pop("start", None)
        view.pop("end", None)
    return view


def transaction_date(tx, tz=UTC) -> date:
    if tx.purchase_date is not None:
        return tx.purchase_date
    created = tx.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created.astimezone(tz).date()


def signed_amount(tx):
    amount = money(tx.amount)
    return -abs(amount) if getattr(tx, "is_refund", False) else amount


def is_refund(tx):
    return not tx.is_transfer and signed_amount(tx) < ZERO


def is_financial(tx):
    return not tx.is_transfer and signed_amount(tx) != ZERO


def is_expense(tx) -> bool:
    return not tx.is_transfer and signed_amount(tx) > ZERO


def owned(transactions, user_id: int) -> list:
    """Повторная проверка владельца перед любым отчётом/экспортом."""
    return [tx for tx in transactions if tx.user_id == user_id]


@dataclass
class Summary:
    count: int = 0
    transfers: int = 0
    refunds: int = 0
    gross: dict = field(default_factory=lambda: defaultdict(Decimal))
    refunded: dict = field(default_factory=lambda: defaultdict(Decimal))
    totals: dict = field(default_factory=lambda: defaultdict(Decimal))
    by_category: dict = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Decimal))
    )
    by_card: dict = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Decimal))
    )
    by_merchant: dict = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Decimal))
    )
    category_counts: Counter = field(default_factory=Counter)
    merchant_counts: Counter = field(default_factory=Counter)
    currency_counts: Counter = field(default_factory=Counter)


def build_summary(transactions) -> Summary:
    """Расходы за вычетом возвратов: отрицательная сумма или is_refund.

    Положительную запись без признака возврата считаем покупкой: менять
    договор команды о хранении операций или угадывать по тексту нельзя.
    """
    result = Summary()
    for tx in transactions:
        if tx.is_transfer:
            result.transfers += 1
        if not is_financial(tx):
            continue
        amount = signed_amount(tx)
        if amount < ZERO:
            result.refunds += 1
            result.refunded[tx.currency] -= amount
        else:
            result.count += 1
            result.gross[tx.currency] += amount
            result.currency_counts[tx.currency] += 1
            result.category_counts[tx.category] += 1
            result.merchant_counts[tx.merchant] += 1
        result.totals[tx.currency] += amount
        result.by_category[tx.category][tx.currency] += amount
        result.by_card[tx.card_id][tx.currency] += amount
        result.by_merchant[tx.merchant][tx.currency] += amount
    return result


def currency_groups(groups: dict, currency: str) -> list:
    return sorted(
        (
            (name, values[currency])
            for name, values in groups.items()
            if values.get(currency, ZERO) != ZERO
        ),
        key=lambda pair: (-pair[1], str(pair[0])),
    )


def format_summary(summary: Summary, cards, period: str) -> str:
    lines = [
        f"Расходы: {period}",
        f"Покупок: {summary.count}; возвратов: {summary.refunds}",
    ]
    names = {card.id: card.name for card in cards}
    if not summary.count and not summary.refunds:
        lines.append("Расходов за этот период нет.")
    for currency, total in sorted(summary.totals.items()):
        lines += [
            "",
            "Итого с учётом возвратов: " + display_money(total, currency),
        ]
        if summary.currency_counts[currency]:
            average = (
                summary.gross[currency] / summary.currency_counts[currency]
            )
            lines.append(
                "Средний чек покупок: " + display_money(average, currency)
            )
        if summary.refunded[currency]:
            lines.append(
                "Возвращено: "
                + display_money(summary.refunded[currency], currency)
            )
        groups = currency_groups(summary.by_category, currency)
        shares = total > ZERO and all(amount >= ZERO for _, amount in groups)
        lines.append("По категориям:")
        for category, amount in groups:
            share = f" ({amount / total:.0%})" if shares else ""
            lines.append(
                f"• {category}: {display_money(amount, currency)}{share}"
            )
        lines.append("По картам:")
        for card_id, amount in currency_groups(summary.by_card, currency):
            lines.append(
                f"• {names.get(card_id, 'Без карты')}:"
                f" {display_money(amount, currency)}"
            )
        lines.append("Магазины с наибольшими расходами:")
        for merchant, amount in currency_groups(summary.by_merchant, currency)[
            :3
        ]:
            lines.append(f"• {merchant}: {display_money(amount, currency)}")
    if summary.transfers:
        lines.append(
            "Переводов между своими счетами, исключённых из расходов:"
            f" {summary.transfers}"
        )
    return "\n".join(lines)


def summary_for_prompt(
    summary: Summary, cards, period="выбранный период"
) -> str:
    return format_summary(summary, cards, period)


def format_metrics(transactions) -> str:
    expenses = [tx for tx in transactions if is_expense(tx)]
    unchanged = sum(
        not getattr(tx, "category_edited", False) for tx in expenses
    )
    rate = f"{unchanged / len(expenses):.0%}" if expenses else "нет данных"
    times = [
        float(money(getattr(tx, "processing_time", 0))) for tx in transactions
    ]
    return (
        f"Операций: {len(transactions)}\nПереводов:"
        f" {sum(tx.is_transfer for tx in transactions)}\nМедианное время"
        " обработки:"
        f" {median_or_zero(times):.2f}"
        f" с\nКатегория не исправлена пользователем: {rate}"
    )


def period_bounds(
    view: dict, today: date | None = None
) -> tuple[date | None, date]:
    today = today or local_today(user_timezone(view.get("timezone", "+00:00")))
    end = today + timedelta(days=1)
    preset = view.get("period", "30")
    if preset == "custom":
        message = (
            "Укажи две даты ГГГГ-ММ-ДД: начало не позже конца и конец не в"
            " будущем; период — до 10 лет."
        )
        try:
            start, last = (
                date.fromisoformat(view["start"]),
                date.fromisoformat(view["end"]),
            )
            if last < start or last > today or (last - start).days > 3660:
                raise ValueError(message)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(message) from error
        return start, last + timedelta(days=1)
    if preset == "all":
        return None, end
    if preset == "today":
        return today, end
    if preset == "yesterday":
        return today - timedelta(days=1), today
    if preset == "week":
        return today - timedelta(days=today.weekday()), end
    if preset == "month":
        return today.replace(day=1), end
    if preset == "previous_month":
        end = today.replace(day=1)
        return (end - timedelta(days=1)).replace(day=1), end
    if preset == "year":
        return today.replace(month=1, day=1), end
    if preset not in {"7", "30", "90"}:
        raise ValueError("Неизвестный период.")
    return end - timedelta(days=int(preset)), end


def period_title(view: dict, today=None) -> str:
    start, end = period_bounds(view, today)
    label = f"{start:%d.%m.%Y}" if start else "начало истории"
    return f"{label} — {end - timedelta(days=1):%d.%m.%Y}"


def select_transactions(
    transactions, view: dict, today=None, *, user_id: int, previous=False
) -> list:
    start, end = period_bounds(view, today)
    if previous:
        if start is None:
            raise ValueError("Для сравнения выбери ограниченный период.")
        start, end = start - (end - start), start
    selected = []
    tz = user_timezone(view.get("timezone", "+00:00"))
    for tx in owned(transactions, user_id):
        day = transaction_date(tx, tz)
        if day >= end or (start is not None and day < start):
            continue
        if view.get("category") and tx.category != view["category"]:
            continue
        if view.get("currency") and tx.currency != view["currency"]:
            continue
        if "card_id" in view and tx.card_id != view["card_id"]:
            continue
        query = normalize_merchant(view.get("merchant", ""))
        if query and query not in normalize_merchant(tx.merchant):
            continue
        selected.append(tx)
    return selected


def comparison_rows(current, previous) -> list[dict]:
    rows = []
    for category in sorted(
        set(current.by_category) | set(previous.by_category)
    ):
        now = current.by_category.get(category, {})
        before = previous.by_category.get(category, {})
        for currency in sorted(set(now) | set(before)):
            new, old = now.get(currency, ZERO), before.get(currency, ZERO)
            change = new - old
            percent = change / old * 100 if old > ZERO else None
            rows.append(
                {
                    "category": category,
                    "currency": currency,
                    "now": new,
                    "before": old,
                    "change": change,
                    "percent": percent,
                }
            )
    return sorted(
        rows,
        key=lambda row: (row["currency"], -row["change"], row["category"]),
    )


def format_comparison(current, previous) -> str:
    lines = ["Сравнение с предыдущим периодом той же длины."]
    for row in comparison_rows(current, previous):
        if row["change"] == ZERO:
            continue
        change = display_money(row["change"], row["currency"])
        rate = (
            f"{row['percent']:+.1f}%"
            if row["percent"] is not None
            else "нет базы для процента"
        )
        lines.append(
            f"{row['category']}:"
            f" {display_money(row['before'], row['currency'])} →"
            f" {display_money(row['now'], row['currency'])}; изменение"
            f" {change} ({rate})."
        )
    if len(lines) == 1:
        lines.append("Изменений по категориям нет.")
    return "\n".join(lines)


def time_series(transactions, weekly=False, tz=UTC) -> dict:
    groups = defaultdict(lambda: defaultdict(Decimal))
    for tx in transactions:
        if is_financial(tx):
            day = transaction_date(tx, tz)
            if weekly:
                day -= timedelta(days=day.weekday())
            groups[tx.currency][day] += signed_amount(tx)
    return {
        currency: dict(sorted(values.items()))
        for currency, values in sorted(groups.items())
    }


def forecast_month(transactions, today: date, tz=UTC) -> dict:
    if today.day < 5:
        raise ValueError(
            "Прогноз доступен с 5-го числа: в начале месяца данных"
            " недостаточно."
        )
    start = today.replace(day=1)
    totals = build_summary(
        [
            tx
            for tx in transactions
            if start <= transaction_date(tx, tz) <= today
        ]
    ).totals
    days = Decimal(calendar.monthrange(today.year, today.month)[1])
    return {
        code: (total * days / today.day).quantize(CENT, rounding=ROUND_HALF_UP)
        for code, total in totals.items()
    }


def subscription_key(merchant: str, currency: str, card_id) -> str:
    # Цена не входит в ключ: отметка «важна» сохраняется при её изменении.
    return stable_key(f"{normalize_merchant(merchant)}|{currency}|{card_id}")


def add_month(day: date, step=1, anchor=None) -> date:
    index = day.year * 12 + day.month - 1 + step
    year, month = divmod(index, 12)
    month += 1
    return date(
        year,
        month,
        min(anchor or day.day, calendar.monthrange(year, month)[1]),
    )


def detect_subscriptions(
    transactions, today: date | None = None, tz=UTC
) -> list[dict]:
    """Совпадение сервиса, валюты, карты, суммы и календарного ритма."""
    today = today or local_today(tz)
    groups = defaultdict(list)
    for tx in transactions:
        if is_expense(tx) and transaction_date(tx, tz) <= today:
            groups[
                subscription_key(tx.merchant, tx.currency, tx.card_id)
            ].append(tx)
    result = []
    for key, values in groups.items():
        values.sort(key=lambda tx: (transaction_date(tx, tz), tx.id))
        last = values[-1]
        amount = money(last.amount)
        tolerance = max(CENT, amount * Decimal("0.03"))
        matches = [
            tx for tx in values if abs(money(tx.amount) - amount) <= tolerance
        ]
        dates = sorted({transaction_date(tx, tz) for tx in matches})
        if len(dates) < 2:
            continue
        # Проверяем последние четыре списания, старый нерегулярный платёж
        # не мешает распознать новую серию подписки.
        dates = dates[-4:]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        monthly = all(
            abs((b - add_month(a, anchor=max(d.day for d in dates))).days) <= 3
            for a, b in zip(dates, dates[1:], strict=False)
        )
        longer = next(
            (
                step
                for step in (3, 6, 12)
                if all(
                    abs(
                        (
                            b
                            - add_month(
                                a, step, anchor=max(d.day for d in dates)
                            )
                        ).days
                    )
                    <= 3
                    for a, b in zip(dates, dates[1:], strict=False)
                )
            ),
            None,
        )
        if monthly:
            cadence, interval = "ежемесячно", 31
            next_date = add_month(dates[-1], anchor=max(d.day for d in dates))
        elif len(dates) >= 3 and all(6 <= gap <= 8 for gap in gaps):
            cadence, interval = "еженедельно", 7
            next_date = dates[-1] + timedelta(days=7)
        elif longer:
            cadence, interval = {
                3: ("ежеквартально", 92),
                6: ("раз в полгода", 184),
                12: ("ежегодно", 366),
            }[longer]
            next_date = add_month(
                dates[-1], longer, anchor=max(d.day for d in dates)
            )
        else:
            continue
        if (today - dates[-1]).days > interval + 7:
            continue
        first_match = min(transaction_date(tx, tz) for tx in matches)
        trial = any(
            ZERO < money(tx.amount) < amount / 4
            and 0 < (first_match - transaction_date(tx, tz)).days <= 35
            for tx in values
        )
        result.append(
            {
                "key": key,
                "merchant": last.merchant,
                "currency": last.currency,
                "category": last.category,
                "card_id": last.card_id,
                "amount": str(amount),
                "cadence": cadence,
                "count": len(dates),
                "last": dates[-1].isoformat(),
                "next": next_date.isoformat(),
                "trial": trial,
                "date_estimated": any(
                    tx.purchase_date is None for tx in matches
                ),
            }
        )
    return sorted(result, key=lambda sub: (sub["currency"], sub["merchant"]))


def gaming_purchase(tx) -> bool:
    text = f"{tx.merchant} {getattr(tx, 'description', '')}".lower()
    return any(word in text for word in GAME_WORDS)


class _RollingWindow:
    """Скользящая медиана через дерево Фенвика: add/remove/kth — O(log n)."""

    def __init__(self, amounts):
        self.values = sorted(set(amounts))
        self.indices = {amount: i + 1 for i, amount in enumerate(self.values)}
        self.tree = [0] * (len(self.values) + 1)
        self.queue = deque()
        self.categories = Counter()
        self.games = 0

    def update(self, tx, delta):
        i = self.indices[money(tx.amount)]
        while i < len(self.tree):
            self.tree[i] += delta
            i += i & -i
        self.categories[tx.category] += delta
        self.games += int(gaming_purchase(tx)) * delta

    def kth(self, rank):
        index, bit = 0, 1 << (len(self.tree).bit_length() - 1)
        while bit:
            nxt = index + bit
            if nxt < len(self.tree) and self.tree[nxt] < rank:
                rank -= self.tree[nxt]
                index = nxt
            bit >>= 1
        return self.values[index]

    def typical(self):
        size = len(self.queue)
        return (self.kth((size + 1) // 2) + self.kth((size + 2) // 2)) / 2


def detect_anomalies(
    transactions, today: date | None = None, acknowledged=(), tz=UTC
) -> list[dict]:
    """O(n log n), история ограничена 180 днями отдельно по карте/валюте."""
    today = today or local_today(tz)
    history = sorted(
        [
            tx
            for tx in transactions
            if is_expense(tx) and transaction_date(tx, tz) <= today
        ],
        key=lambda tx: (transaction_date(tx, tz), tx.id),
    )
    values = defaultdict(list)
    for tx in history:
        values[(tx.currency, tx.card_id)].append(money(tx.amount))
    windows = {key: _RollingWindow(amounts) for key, amounts in values.items()}
    acknowledged = set(acknowledged)
    result = []
    for tx in history:
        day = transaction_date(tx, tz)
        window = windows[(tx.currency, tx.card_id)]
        while window.queue and (day - window.queue[0][0]).days > 180:
            _, old = window.queue.popleft()
            window.update(old, -1)
        reasons = []
        ready = (
            len(window.queue) >= 5 and (day - window.queue[0][0]).days >= 14
        )
        if ready and tx.id not in acknowledged and (today - day).days <= 30:
            typical = window.typical()
            if typical > ZERO and money(tx.amount) >= typical * 5:
                reasons.append(
                    "Сумма как минимум в 5 раз выше типичной для этой карты и"
                    " валюты."
                )
            if gaming_purchase(tx) and not window.games:
                reasons.append(
                    "Первый замеченный игровой платёж в доступной истории этой"
                    " карты."
                )
            if len(window.queue) >= 20 and not window.categories[tx.category]:
                reasons.append(
                    "Категория покупки раньше не встречалась в доступной"
                    " истории этой карты."
                )
        if reasons:
            result.append(
                {
                    "id": tx.id,
                    "merchant": tx.merchant,
                    "currency": tx.currency,
                    "amount": str(money(tx.amount)),
                    "date": day.isoformat(),
                    "reasons": reasons,
                }
            )
        window.queue.append((day, tx))
        window.update(tx, 1)
    return list(reversed(result))


async def get_options(user_id: int) -> dict:
    from sqlalchemy import text
    from finfly.db.engine import engine

    async with engine.begin() as connection:
        await connection.execute(text(STATE_DDL))
        rows = (
            await connection.execute(
                text(
                    "SELECT name, value FROM analysis_settings WHERE"
                    " user_id=:uid"
                ),
                {"uid": user_id},
            )
        ).all()
    result = {}
    for name, value in rows:
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            logger.warning(
                "Повреждённая настройка аналитики пропущена: %s", name
            )
            continue
        expected = {
            "stats_view": dict,
            "cashback_rules": list,
            "protected_categories": list,
            "preferences": str,
            "goal": str,
            "timezone": str,
            "advice_detail": str,
        }
        if name in expected and not isinstance(decoded, expected[name]):
            logger.warning("Неверный тип настройки аналитики: %s", name)
            continue
        if name == "timezone":
            try:
                user_timezone(decoded)
            except ValueError:
                logger.warning("Неверный часовой пояс аналитики пропущен")
                continue
        if name == "protected_categories" and any(
            not isinstance(v, str) for v in decoded
        ):
            logger.warning("Неверный список категорий аналитики пропущен")
            continue
        if name.startswith("subscription:") and decoded not in (
            "important",
            "dismissed",
            "review",
        ):
            continue
        if name == "stats_view":
            decoded = {
                key: item
                for key, item in decoded.items()
                if key
                in {
                    "period",
                    "start",
                    "end",
                    "category",
                    "currency",
                    "merchant",
                    "card_id",
                }
            }
            if any(
                not isinstance(v, str)
                for k, v in decoded.items()
                if k != "card_id"
            ) or (
                "card_id" in decoded
                and decoded["card_id"] is not None
                and type(decoded["card_id"]) is not int
            ):
                logger.warning("Неверный фильтр аналитики пропущен")
                continue
        if name == "cashback_rules":
            try:
                for rule in decoded:
                    if (
                        not isinstance(rule, dict)
                        or type(rule.get("card_id")) is not int
                    ):
                        raise ValueError("Неверная карта")
                    from types import SimpleNamespace

                    parse_cashback_rule(
                        ";".join(
                            str(rule[k]) if rule[k] is not None else "нет"
                            for k in (
                                "card_id",
                                "currency",
                                "category",
                                "rate",
                                "cap",
                            )
                        ),
                        [SimpleNamespace(id=rule["card_id"])],
                    )
            except (KeyError, ValueError, TypeError):
                logger.warning("Повреждённые условия кэшбэка пропущены")
                continue
        result[name] = decoded
    return result


async def set_option(user_id: int, name: str, value) -> None:
    from sqlalchemy import text
    from finfly.db.engine import engine

    async with engine.begin() as connection:
        await connection.execute(text(STATE_DDL))
        if name == "feedback:last":
            # Ограничиваем обратную связь одной оценкой и убираем старые ключи.
            await connection.execute(
                text(
                    "DELETE FROM analysis_settings WHERE user_id=:uid AND name"
                    " LIKE 'feedback:%'"
                ),
                {"uid": user_id},
            )
        await connection.execute(
            text(
                "INSERT INTO analysis_settings (user_id, name, value) VALUES"
                " (:uid, :name, :value) ON CONFLICT(user_id, name) DO UPDATE"
                " SET value=excluded.value"
            ),
            {
                "uid": user_id,
                "name": name,
                "value": json.dumps(value, ensure_ascii=False),
            },
        )


STATE_DDL = (
    "CREATE TABLE IF NOT EXISTS analysis_settings ("
    "user_id BIGINT NOT NULL, name VARCHAR(80) NOT NULL, value TEXT NOT NULL, "
    "PRIMARY KEY (user_id, name))"
)


def cashback_estimate(transactions, cards, rules: list[dict], tz=UTC) -> dict:
    """Сценарий по введённым пользователем ставкам и месячным лимитам.

    Лимит относится к одному правилу; одинаковый счётчик используется для
    всех категорий правила '*'. planned — жадный сценарий по порядку покупок,
    не глобальный оптимум. Неизвестные правила возврата кэшбэка не угадываются.
    """
    if any(is_refund(tx) for tx in transactions):
        raise ValueError(
            "В периоде есть возвраты: для кэшбэка нужны правила банка об их"
            " учёте."
        )
    keys = [
        (r.get("card_id"), r.get("currency"), r.get("category")) for r in rules
    ]
    if len(set(keys)) != len(keys):
        raise ValueError(
            "Удали повторные правила кэшбэка для одной карты, валюты и"
            " категории."
        )
    card_names = {card.id: card.name for card in cards}
    rules = [r for r in rules if r.get("card_id") in card_names]
    actual_used, planned_used = defaultdict(Decimal), defaultdict(Decimal)
    actual, planned = defaultdict(Decimal), defaultdict(Decimal)
    allocation = Counter()

    def reward(tx, card_id, used):
        applicable = [
            (i, r)
            for i, r in enumerate(rules)
            if r["card_id"] == card_id
            and r["currency"] == tx.currency
            and r["category"] in ("*", tx.category)
        ]
        exact = [
            pair for pair in applicable if pair[1]["category"] == tx.category
        ]
        applicable = exact or applicable
        if not applicable:
            return ZERO, None
        i, rule = applicable[0]
        key = (i, transaction_date(tx, tz).strftime("%Y-%m"))
        value = money(tx.amount) * money(rule["rate"]) / 100
        if rule.get("cap") is not None:
            value = min(value, max(ZERO, money(rule["cap"]) - used[key]))
        return value.quantize(CENT, rounding=ROUND_HALF_UP), key

    for tx in sorted(
        transactions, key=lambda tx: (transaction_date(tx, tz), tx.id)
    ):
        if not is_expense(tx):
            continue
        value, key = reward(tx, tx.card_id, actual_used)
        if key is not None:
            actual_used[key] += value
        actual[tx.currency] += value
        candidates = [
            (reward(tx, card_id, planned_used), card_id)
            for card_id in card_names
        ]
        if candidates:
            (best, key), card_id = max(
                candidates, key=lambda pair: (pair[0][0], -pair[1])
            )
            if key is not None:
                planned_used[key] += best
            planned[tx.currency] += best
            if best > ZERO:
                allocation[
                    (tx.category, tx.currency, card_names[card_id])
                ] += 1
    return {
        "actual": dict(actual),
        "planned": dict(planned),
        "allocation": dict(allocation),
    }


def parse_cashback_rule(value: str, cards) -> dict:
    parts = [part.strip() for part in value.split(";")]
    if len(parts) != 5:
        raise ValueError(
            "Формат: ID карты; валюта; категория или *; процент; месячный"
            " лимит или нет"
        )
    card_id = positive_int(parts[0], "ID карты должен быть целым числом.")
    if card_id not in {card.id for card in cards}:
        raise ValueError("Карта не найдена среди твоих карт.")
    currency = parts[1].upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValueError("Валюта — три латинские буквы, например RUB.")
    try:
        rate = Decimal(parts[3].replace(",", "."))
        cap = (
            None
            if parts[4].lower() == "нет"
            else Decimal(parts[4].replace(",", "."))
        )
    except InvalidOperation as error:
        raise ValueError("Процент и лимит должны быть числами.") from error
    if not rate.is_finite() or not ZERO <= rate <= 100:
        raise ValueError("Процент должен быть от 0 до 100.")
    if cap is not None and (not cap.is_finite() or cap < ZERO):
        raise ValueError("Лимит должен быть неотрицательным.")
    if not parts[2] or len(parts[2]) > 64:
        raise ValueError("Укажи категорию длиной до 64 символов.")
    return {
        "card_id": card_id,
        "currency": currency,
        "category": parts[2],
        "rate": str(rate),
        "cap": str(cap) if cap is not None else None,
    }


def split_text(text: str, limit=3000) -> list[str]:
    """Лимит UTF-16: для текста 3000, для caption передать limit=1024."""
    if limit < 2:
        raise ValueError("Лимит должен быть не меньше 2 UTF-16 единиц.")
    parts, current, size = [], [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if current and size + width > limit:
            parts.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += width
    if current:
        parts.append("".join(current))
    return parts or ["Нет данных."]


def export_rows(transactions, cards, tz=UTC) -> list[list]:
    names = {c.id: c.name for c in cards}
    rows = [
        [
            "ID",
            "Дата покупки/добавления",
            "Магазин",
            "Категория",
            "Сумма",
            "Валюта",
            "Карта",
        ]
    ]
    for tx in sorted(
        transactions, key=lambda tx: (transaction_date(tx, tz), tx.id)
    ):
        if is_financial(tx):
            rows.append(
                [
                    str(tx.id),
                    transaction_date(tx, tz).isoformat(),
                    tx.merchant,
                    tx.category,
                    str(
                        signed_amount(tx).quantize(
                            CENT, rounding=ROUND_HALF_UP
                        )
                    ),
                    tx.currency,
                    names.get(tx.card_id, "Без карты"),
                ]
            )
    return rows


def csv_bytes(transactions, cards, tz=UTC) -> bytes:
    # Апостроф виден в CSV: это сознательная защита от формул при открытии.
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";")
    for row in export_rows(transactions, cards, tz):
        safe = [
            (
                "'" + value
                if index != 4
                and value.lstrip().startswith(("=", "+", "-", "@"))
                else value
            )
            for index, value in enumerate(row)
        ]
        writer.writerow(safe)
    return buffer.getvalue().encode("utf-8-sig")


def xlsx_bytes(transactions, cards, tz=UTC) -> bytes:
    """XLSX без внешней библиотеки; текст не становится формулой."""
    rows = []
    for number, row in enumerate(export_rows(transactions, cards, tz), 1):
        cells = []
        for index, value in enumerate(row):
            reference = f"{col_letter(index + 1)}{number}"
            clean = "".join(
                c for c in str(value) if ord(c) >= 32 or c in "\n\t"
            )
            if number > 1 and index == 4:
                cells.append(f'<c r="{reference}"><v>{escape(clean)}</v></c>')
            else:
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t'
                    f' xml:space="preserve">{escape(clean)}</t></is></c>'
                )
        rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_ns = "http://schemas.openxmlformats.org/package/2006"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    package_mime = "application/vnd.openxmlformats-package"
    parts = {
        "[Content_Types].xml": (
            "<Types"
            f' xmlns="{package_ns}/content-types"><Default'
            ' Extension="rels"'
            f' ContentType="{package_mime}.relationships+xml"/><Default'
            ' Extension="xml" ContentType="application/xml"/><Override'
            ' PartName="/xl/workbook.xml"'
            f' ContentType="{mime}.sheet.main+xml"/><Override'
            ' PartName="/xl/worksheets/sheet1.xml"'
            f' ContentType="{mime}.worksheet+xml"/></Types>'
        ),
        "_rels/.rels": (
            "<Relationships"
            f' xmlns="{package_ns}/relationships"><Relationship'
            f' Id="rId1" Type="{rel}/officeDocument"'
            ' Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet'
            ' name="Расходы" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            "<Relationships"
            f' xmlns="{package_ns}/relationships"><Relationship'
            f' Id="rId1" Type="{rel}/worksheet"'
            ' Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": (
            "<worksheet"
            f' xmlns="{ns}"><sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft"'
            ' state="frozen"/>'
            "</sheetView></sheetViews>"
            f"<sheetData>{''.join(rows)}</sheetData></worksheet>"
        ),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in parts.items():
            archive.writestr(
                path, '<?xml version="1.0" encoding="UTF-8"?>' + content
            )
    return output.getvalue()


def chart_svg(
    values: dict, currency: str, title: str, *, group_total=None
) -> bytes:
    """SVG: валюты раздельно, возвраты слева от нуля, доступные подписи."""
    rows = list(values.items())
    height = max(230, 110 + len(rows) * 38)
    maximum = max(
        (abs(money(v)) for _, v in rows), default=Decimal(1)
    ) or Decimal(1)
    total = (
        money(group_total)
        if group_total is not None
        else sum((money(v) for _, v in rows), ZERO)
    )
    negative = any(money(value) < ZERO for _, value in rows)
    origin, scale = (515, 215) if negative else (300, 430)
    description = (
        "; ".join(
            f"{label}: {display_money(value, currency)}"
            for label, value in rows
        )
        or "Операций за период нет."
    )
    parts = [
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1000"'
            f' height="{height}" viewBox="0 0 1000 {height}" role="img"'
            ' aria-labelledby="title desc">'
        ),
        f'<title id="title">{escape(title)} · {escape(currency)}</title>',
        f'<desc id="desc">{escape(description)}</desc>',
        '<rect width="100%" height="100%" fill="#101827"/>',
        '<g font-family="Arial,sans-serif" fill="#f1f5f9">',
        (
            f'<text x="24" y="36" font-size="22">{escape(title)} ·'
            f" {escape(currency)}</text>"
        ),
    ]
    if negative:
        parts.append(
            f'<path d="M{origin} 60 V{height - 30}" stroke="#94a3b8"/>'
        )
    for index, (label, value) in enumerate(rows):
        y = 75 + index * 38
        amount = money(value)
        width = float(abs(amount) / maximum) * scale
        x = origin - width if amount < ZERO else origin
        color = "#fb923c" if amount < ZERO else "#38bdf8"
        full_label = str(label)
        short_label = (
            full_label if len(full_label) <= 32 else full_label[:31] + "…"
        )
        share = (
            f" ({amount / total:.0%})"
            if not negative and total > ZERO and ZERO <= amount <= total
            else ""
        )
        parts += [
            (
                f'<text x="24" y="{y + 19}"'
                ' font-size="14">'
                f"<title>{escape(full_label)}</title>"
                f"{escape(short_label)}</text>"
            ),
            (
                f'<rect x="{x:.2f}" y="{y}" width="{width:.2f}" height="26"'
                f' rx="4" fill="{color}"/>'
            ),
            (
                f'<text x="750" y="{y + 19}"'
                ' font-size="14">'
                f"{escape(display_money(value, currency))}{share}</text>"
            ),
        ]
    if not rows:
        parts.append('<text x="24" y="90">Операций за период нет.</text>')
    parts.append("</g></svg>")
    return "".join(parts).encode()


async def load_transactions(user_id, *, start=None, end=None, tz=UTC):
    """SQL отбирает владельца и даты; время добавления хранится в UTC."""
    from sqlalchemy import and_, or_, select
    from finfly.db.engine import session_maker
    from finfly.db.models import Transaction

    query = select(Transaction).where(Transaction.user_id == user_id)
    if start is not None:
        utc_start = (
            datetime.combine(start, datetime.min.time(), tz)
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
        query = query.where(
            or_(
                Transaction.purchase_date >= start,
                and_(
                    Transaction.purchase_date.is_(None),
                    Transaction.created_at >= utc_start,
                ),
            )
        )
    if end is not None:
        utc_end = (
            datetime.combine(end, datetime.min.time(), tz)
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
        query = query.where(
            or_(
                Transaction.purchase_date < end,
                and_(
                    Transaction.purchase_date.is_(None),
                    Transaction.created_at < utc_end,
                ),
            )
        )
    async with session_maker() as session:
        return list(await session.scalars(query))


def positive_int(value, message="Нужно целое неотрицательное число."):
    try:
        number = int(value)
        if number < 0:
            raise ValueError(message)
        return number
    except (ValueError, TypeError) as error:
        raise ValueError(message) from error


_throttle = {}


def allow_action(namespace, user_id, *, interval, clock=None):
    now = time.monotonic() if clock is None else clock
    if len(_throttle) > 2000:
        for key, last in list(_throttle.items()):
            if now - last > 3600:
                del _throttle[key]
    key = (namespace, user_id)
    if now - _throttle.get(key, -float("inf")) < interval:
        return False
    _throttle[key] = now
    return True


def col_letter(index):
    if index < 1:
        raise ValueError("Номер столбца начинается с 1.")
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result
