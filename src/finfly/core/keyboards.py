from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from finfly.core.constants import CATEGORY_NAMES, STATS_PERIODS
from finfly.core.texts import (
    BTN_ADD_CARD,
    BTN_ADVICE,
    BTN_BACK,
    BTN_CARDS,
    BTN_DELETE_CARD,
    BTN_HELP,
    BTN_HISTORY,
    BTN_NO_CARD,
    BTN_SKIP,
    BTN_STATS,
    BTN_TX_CARD,
    BTN_TX_CATEGORY,
    BTN_TX_COUNT,
    BTN_TX_DELETE,
    MENU_PLACEHOLDER,
)
from finfly.core.utils import category_icon, format_money
from finfly.db.models import Card, Transaction

###
### Callback data
###


class TxAction(CallbackData, prefix="tx"):
    """Действие с операцией: open, category, card, delete, count, back."""

    action: str
    tx_id: int


class TxCategory(CallbackData, prefix="txcat"):
    """Выбор категории операции (индекс в CATEGORY_NAMES)."""

    tx_id: int
    index: int


class TxCard(CallbackData, prefix="txcard"):
    """Выбор карты операции (0 — без карты)."""

    tx_id: int
    card_id: int


class StatsPeriod(CallbackData, prefix="stats"):
    """Период сводки в днях (0 — всё время)."""

    days: int


class CardAction(CallbackData, prefix="card"):
    """Действие с картами: add, delete."""

    action: str
    card_id: int = 0


SKIP_CALLBACK = "skip"

###
### Клавиатуры
###


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню под полем ввода."""
    builder = ReplyKeyboardBuilder()
    for text in (BTN_STATS, BTN_HISTORY, BTN_CARDS, BTN_ADVICE, BTN_HELP):
        builder.button(text=text)
    builder.adjust(2, 2, 1)
    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder=MENU_PLACEHOLDER,
    )


def transaction_kb(tx: Transaction) -> InlineKeyboardMarkup:
    """Кнопки под карточкой операции."""
    builder = InlineKeyboardBuilder()
    if tx.is_transfer:
        builder.button(
            text=BTN_TX_COUNT,
            callback_data=TxAction(action="count", tx_id=tx.id),
        )
    else:
        builder.button(
            text=BTN_TX_CATEGORY,
            callback_data=TxAction(action="category", tx_id=tx.id),
        )
        builder.button(
            text=BTN_TX_CARD,
            callback_data=TxAction(action="card", tx_id=tx.id),
        )
    builder.button(
        text=BTN_TX_DELETE,
        callback_data=TxAction(action="delete", tx_id=tx.id),
    )
    builder.adjust(1 if tx.is_transfer else 2, 1)
    return builder.as_markup()


def back_button(builder: InlineKeyboardBuilder, tx_id: int) -> None:
    """Добавляет кнопку возврата к карточке операции."""
    builder.button(
        text=BTN_BACK,
        callback_data=TxAction(action="back", tx_id=tx_id),
    )


def categories_kb(tx_id: int) -> InlineKeyboardMarkup:
    """Выбор категории операции."""
    builder = InlineKeyboardBuilder()
    for index, category in enumerate(CATEGORY_NAMES):
        builder.button(
            text=f"{category_icon(category)} {category}",
            callback_data=TxCategory(tx_id=tx_id, index=index),
        )
    back_button(builder, tx_id)
    builder.adjust(2)
    return builder.as_markup()


def tx_cards_kb(tx_id: int, cards: list[Card]) -> InlineKeyboardMarkup:
    """Выбор карты, которой оплачена операция."""
    builder = InlineKeyboardBuilder()
    for card in cards:
        builder.button(
            text=f"💳 {card.name}",
            callback_data=TxCard(tx_id=tx_id, card_id=card.id),
        )
    builder.button(
        text=BTN_NO_CARD,
        callback_data=TxCard(tx_id=tx_id, card_id=0),
    )
    back_button(builder, tx_id)
    builder.adjust(1)
    return builder.as_markup()


def history_kb(transactions: list[Transaction]) -> InlineKeyboardMarkup:
    """Список последних операций, каждая открывается по нажатию."""
    builder = InlineKeyboardBuilder()
    for tx in transactions:
        icon = "🔁" if tx.is_transfer else category_icon(tx.category)
        builder.button(
            text=(
                f"{icon} {tx.merchant[:24]} · "
                f"{format_money(tx.amount, tx.currency)}"
            ),
            callback_data=TxAction(action="open", tx_id=tx.id),
        )
    builder.adjust(1)
    return builder.as_markup()


def stats_period_kb(current: int) -> InlineKeyboardMarkup:
    """Переключатель периода сводки."""
    builder = InlineKeyboardBuilder()
    for days, title in STATS_PERIODS.items():
        builder.button(
            text=f"• {title} •" if days == current else title,
            callback_data=StatsPeriod(days=days),
        )
    return builder.as_markup()


def cards_kb(cards: list[Card]) -> InlineKeyboardMarkup:
    """Управление картами пользователя."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BTN_ADD_CARD,
        callback_data=CardAction(action="add"),
    )
    for card in cards:
        builder.button(
            text=BTN_DELETE_CARD.format(name=card.name),
            callback_data=CardAction(action="delete", card_id=card.id),
        )
    builder.adjust(1)
    return builder.as_markup()


def skip_kb() -> InlineKeyboardMarkup:
    """Кнопка пропуска необязательного шага."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_SKIP, callback_data=SKIP_CALLBACK)
    return builder.as_markup()
