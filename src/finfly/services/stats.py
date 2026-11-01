from collections import Counter, defaultdict
from dataclasses import dataclass, field

from finfly.core.texts import (
    METRICS_TEXT,
    NO_CARD,
    STATS_BY_CARD,
    STATS_BY_CATEGORY,
    STATS_EMPTY,
    STATS_TITLE,
    STATS_TOTAL,
    STATS_TRANSFERS,
)
from finfly.core.utils import (
    category_icon,
    escape_md,
    format_amounts,
    median_or_zero,
    progress_bar,
)
from finfly.db.models import Card, Transaction


def amounts_by_currency() -> defaultdict[str, float]:
    return defaultdict(float)


@dataclass
class Summary:
    """Агрегированные расходы пользователя за период."""

    count: int = 0
    transfers: int = 0
    totals: dict[str, float] = field(default_factory=amounts_by_currency)
    by_category: dict[str, dict[str, float]] = field(
        default_factory=lambda: defaultdict(amounts_by_currency),
    )
    by_card: dict[int | None, dict[str, float]] = field(
        default_factory=lambda: defaultdict(amounts_by_currency),
    )
    category_counts: Counter = field(default_factory=Counter)
    merchant_counts: Counter = field(default_factory=Counter)


def build_summary(transactions: list[Transaction]) -> Summary:
    """Считает сводку расходов без переводов между своими счетами."""
    summary = Summary()
    for tx in transactions:
        if tx.is_transfer:
            summary.transfers += 1
            continue
        summary.count += 1
        summary.totals[tx.currency] += tx.amount
        summary.by_category[tx.category][tx.currency] += tx.amount
        summary.by_card[tx.card_id][tx.currency] += tx.amount
        summary.category_counts[tx.category] += 1
        summary.merchant_counts[tx.merchant] += 1
    return summary


def sorted_groups(groups: dict) -> list[tuple]:
    """Сортирует группы по убыванию суммы."""
    return sorted(groups.items(), key=lambda pair: -sum(pair[1].values()))


def card_title(card_id: int | None, cards: dict[int, Card]) -> str:
    """Название карты для отображения."""
    card = cards.get(card_id)
    return escape_md(card.name) if card else NO_CARD


def format_summary(summary: Summary, cards: list[Card], period: str) -> str:
    """Форматирует сводку расходов для сообщения в Telegram."""
    title = STATS_TITLE.format(period=period)
    if not summary.count:
        return title + STATS_EMPTY

    lines = [
        title,
        STATS_TOTAL.format(
            total=format_amounts(summary.totals),
            count=summary.count,
        ),
        STATS_BY_CATEGORY,
    ]
    # Доли считаем, только если все траты в одной валюте
    single_total = (
        next(iter(summary.totals.values()))
        if len(summary.totals) == 1
        else None
    )
    for category, amounts in sorted_groups(summary.by_category):
        lines.append(
            f"{category_icon(category)} {category} — "
            f"*{format_amounts(amounts)}*"
        )
        if single_total:
            share = sum(amounts.values()) / single_total
            lines.append(f"`{progress_bar(share)}` {share:.0%}")

    cards_by_id = {card.id: card for card in cards}
    lines.append(STATS_BY_CARD)
    for card_id, amounts in sorted_groups(summary.by_card):
        lines.append(
            f"💳 {card_title(card_id, cards_by_id)} — "
            f"{format_amounts(amounts)}"
        )

    if summary.transfers:
        lines.append(STATS_TRANSFERS.format(count=summary.transfers))
    return "\n".join(lines)


def summary_for_prompt(summary: Summary, cards: list[Card]) -> str:
    """Готовит сводку и условия карт для Recommendations-агента."""
    lines = [f"Всего расходов: {format_amounts(summary.totals)}", ""]

    lines.append("Расходы по категориям:")
    for category, amounts in sorted_groups(summary.by_category):
        count = summary.category_counts[category]
        lines.append(
            f"- {category}: {format_amounts(amounts)} ({count} опер.)"
        )

    lines.append("\nЧастые магазины:")
    for merchant, count in summary.merchant_counts.most_common(5):
        lines.append(f"- {merchant}: {count} опер.")

    cards_by_id = {card.id: card for card in cards}
    lines.append("\nРасходы по картам:")
    for card_id, amounts in sorted_groups(summary.by_card):
        card = cards_by_id.get(card_id)
        name = card.name if card else "карта не указана"
        lines.append(f"- {name}: {format_amounts(amounts)}")

    lines.append("\nКарты пользователя и условия кэшбэка:")
    for card in cards:
        lines.append(f"- {card.name}: {card.cashback or 'условия не указаны'}")
    if not cards:
        lines.append("- карты не добавлены")
    return "\n".join(lines)


def format_metrics(transactions: list[Transaction]) -> str:
    """Метрики качества работы агентов по операциям пользователя."""
    expenses = [tx for tx in transactions if not tx.is_transfer]
    not_edited = sum(not tx.category_edited for tx in expenses)
    return METRICS_TEXT.format(
        total=len(transactions),
        transfers=len(transactions) - len(expenses),
        median_time=median_or_zero(
            [tx.processing_time for tx in transactions]
        ),
        not_edited=not_edited / len(expenses) if expenses else 0,
    )
