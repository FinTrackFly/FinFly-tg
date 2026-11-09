import re
from datetime import UTC, date, datetime
from statistics import median

from finfly.core.constants import (
    CATEGORIES,
    CURRENCY_SYMBOLS,
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
)

MD_SPECIAL_CHARS = re.compile(r"([_*`\[])")
NOT_WORD_CHARS = re.compile(r"[^\w]+")


def escape_md(text: str) -> str:
    """Экранирует спецсимволы Markdown (legacy) в пользовательском тексте."""
    return MD_SPECIAL_CHARS.sub(r"\\\1", text)


def bold(text: str) -> str:
    """Выделяет пользовательский текст жирным (Markdown legacy).

    Экранирование внутри сущностей не работает, поэтому убираем «*».
    """
    return f"*{text.replace('*', '')}*"


def normalize_merchant(name: str) -> str:
    """Приводит название магазина к ключу для памяти о пользователе."""
    return " ".join(NOT_WORD_CHARS.sub(" ", name.lower()).split())


def category_icon(category: str) -> str:
    """Возвращает иконку категории."""
    return CATEGORIES.get(category, CATEGORIES[DEFAULT_CATEGORY])


def format_money(amount: float, currency: str) -> str:
    """Форматирует сумму: 1234.5, RUB -> «1 234.50 ₽»."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    return f"{amount:,.2f}".replace(",", " ") + f" {symbol}"


def format_amounts(amounts: dict[str, float]) -> str:
    """Форматирует суммы в разных валютах: «56.58 $ + 1 200.00 ₽»."""
    if not amounts:
        return format_money(0, DEFAULT_CURRENCY)
    return " + ".join(
        format_money(amount, currency)
        for currency, amount in sorted(
            amounts.items(),
            key=lambda pair: -pair[1],
        )
    )


def progress_bar(share: float, width: int = 10) -> str:
    """Рисует полосу доли от 0 до 1: ▓▓▓░░░░░░░."""
    filled = round(max(0.0, min(share, 1.0)) * width)
    return "▓" * filled + "░" * (width - filled)


def median_or_zero(values: list[float]) -> float:
    """Медиана списка или 0 для пустого списка."""
    return median(values) if values else 0.0


def parse_date(value: str) -> date | None:
    """Разбирает дату в формате YYYY-MM-DD, иначе None."""
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def utc_now() -> datetime:
    """Текущее время UTC без таймзоны (как хранит БД)."""
    return datetime.now(UTC).replace(tzinfo=None)

# Module implemented by Dmitry Voronov
