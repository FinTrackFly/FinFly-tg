import logging

import httpx
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender
from gigachat.exceptions import RateLimitError

from finfly.core.constants import (
    CATEGORY_NAMES,
    HISTORY_LIMIT,
    MIN_TEXT_LENGTH,
)
from finfly.core.keyboards import (
    TxAction,
    TxCard,
    TxCategory,
    categories_kb,
    history_kb,
    transaction_kb,
    tx_cards_kb,
)
from finfly.core.texts import (
    BTN_HISTORY,
    CATEGORY_SAVED,
    CHOOSE_CARD,
    CHOOSE_CATEGORY,
    HISTORY_EMPTY,
    HISTORY_TITLE,
    NO_CARD,
    NO_CARDS_FOR_TX,
    NOT_RECEIPT,
    RECEIPT_BUSY,
    RECEIPT_ERROR,
    RECEIPT_PROCESSING,
    RECEIPT_TIMEOUT,
    TEXT_TOO_SHORT,
    TX_DELETED,
    TX_ITEMS,
    TX_MORE_ITEMS,
    TX_NOT_FOUND,
    TX_SAVED,
    TX_TEMPLATE,
    TX_TIME,
    TX_TRANSFER,
    UNSUPPORTED_FILE,
)
from finfly.core.utils import bold, category_icon, escape_md, format_money
from finfly.db.models import Card, Transaction
from finfly.db.requests import (
    delete_transaction,
    get_cards,
    get_last_transactions,
    get_transaction,
    set_transaction_category,
    update_transaction,
)
from finfly.services.receipts import process_receipt

router_receipts = Router()
logger = logging.getLogger(__name__)

MAX_SHOWN_ITEMS = 10


def format_transaction(tx: Transaction, cards: list[Card]) -> str:
    """Карточка операции для сообщения."""
    card = next((card for card in cards if card.id == tx.card_id), None)
    shown_date = tx.purchase_date or tx.created_at.date()
    text = (TX_TRANSFER if tx.is_transfer else TX_SAVED) + TX_TEMPLATE.format(
        merchant=bold(tx.merchant),
        description=escape_md(tx.description or tx.merchant),
        amount=format_money(tx.amount, tx.currency),
        icon="🔁" if tx.is_transfer else category_icon(tx.category),
        category=tx.category,
        card=escape_md(card.name) if card else NO_CARD,
        date=shown_date.strftime("%d.%m.%Y"),
    )

    if tx.items:
        lines = [
            f"• {escape_md(item['name'])} ×{item['quantity']:g} — "
            f"{format_money(item['price'], tx.currency)}"
            for item in tx.items[:MAX_SHOWN_ITEMS]
        ]
        if len(tx.items) > MAX_SHOWN_ITEMS:
            lines.append(
                TX_MORE_ITEMS.format(count=len(tx.items) - MAX_SHOWN_ITEMS)
            )
        text += TX_ITEMS.format(items="\n".join(lines))

    if tx.processing_time:
        text += TX_TIME.format(seconds=tx.processing_time)
    return text


async def show_transaction(message: Message, user_id: int, tx_id: int):
    """Перерисовывает сообщение карточкой операции."""
    tx = await get_transaction(user_id, tx_id)
    if tx is None:
        await message.edit_text(TX_DELETED)
        return
    await message.edit_text(
        format_transaction(tx, await get_cards(user_id)),
        reply_markup=transaction_kb(tx),
    )


async def handle_receipt(
    message: Message,
    image: bytes | None = None,
    text: str = "",
):
    """Отправляет чек агентам и показывает результат."""
    reply = await message.answer(RECEIPT_PROCESSING)
    try:
        async with ChatActionSender.typing(
            bot=message.bot,
            chat_id=message.chat.id,
        ):
            tx = await process_receipt(
                message.from_user.id,
                image=image,
                text=text,
            )
    except RateLimitError:
        logger.warning("GigaChat: лимит запросов исчерпан")
        await reply.edit_text(RECEIPT_BUSY)
        return
    except httpx.TimeoutException:
        logger.warning("GigaChat: превышено время ожидания ответа")
        await reply.edit_text(RECEIPT_TIMEOUT)
        return
    except Exception:
        logger.exception("Ошибка обработки чека")
        await reply.edit_text(RECEIPT_ERROR)
        return

    if tx is None:
        await reply.edit_text(NOT_RECEIPT)
        return
    cards = await get_cards(message.from_user.id)
    await reply.edit_text(
        format_transaction(tx, cards),
        reply_markup=transaction_kb(tx),
    )


###
### Приём чеков
###


@router_receipts.message(F.photo)
async def receipt_photo(message: Message):
    """Обрабатывает фото чека."""
    file = await message.bot.download(message.photo[-1])
    await handle_receipt(message, image=file.read())


@router_receipts.message(F.document)
async def receipt_document(message: Message):
    """Обрабатывает чек, отправленный файлом-изображением."""
    mime_type = message.document.mime_type or ""
    if not mime_type.startswith("image/"):
        await message.answer(UNSUPPORTED_FILE)
        return
    file = await message.bot.download(message.document)
    await handle_receipt(message, image=file.read())


@router_receipts.message(Command("history"))
@router_receipts.message(F.text == BTN_HISTORY)
async def cmd_history(message: Message):
    """Показывает последние операции."""
    transactions = await get_last_transactions(
        message.from_user.id,
        HISTORY_LIMIT,
    )
    if not transactions:
        await message.answer(HISTORY_EMPTY)
        return
    await message.answer(HISTORY_TITLE, reply_markup=history_kb(transactions))


@router_receipts.message(
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
)
async def receipt_text(message: Message):
    """Обрабатывает текст операции: SMS, push или строку выписки."""
    if len(message.text) < MIN_TEXT_LENGTH:
        await message.answer(TEXT_TOO_SHORT)
        return
    await handle_receipt(message, text=message.text)


###
### Редактирование операции
###


@router_receipts.callback_query(TxAction.filter(F.action == "open"))
async def tx_open(callback: CallbackQuery, callback_data: TxAction):
    """Открывает операцию из истории отдельным сообщением."""
    user_id = callback.from_user.id
    tx = await get_transaction(user_id, callback_data.tx_id)
    if tx is None:
        await callback.answer(TX_NOT_FOUND, show_alert=True)
        return
    await callback.message.answer(
        format_transaction(tx, await get_cards(user_id)),
        reply_markup=transaction_kb(tx),
    )
    await callback.answer()


@router_receipts.callback_query(TxAction.filter(F.action == "back"))
async def tx_back(callback: CallbackQuery, callback_data: TxAction):
    """Возвращает карточку операции."""
    await show_transaction(
        callback.message,
        callback.from_user.id,
        callback_data.tx_id,
    )
    await callback.answer()


@router_receipts.callback_query(TxAction.filter(F.action == "category"))
async def tx_category_menu(callback: CallbackQuery, callback_data: TxAction):
    """Показывает выбор категории."""
    tx = await get_transaction(callback.from_user.id, callback_data.tx_id)
    if tx is None:
        await callback.answer(TX_NOT_FOUND, show_alert=True)
        return
    await callback.message.edit_text(
        CHOOSE_CATEGORY.format(merchant=bold(tx.merchant)),
        reply_markup=categories_kb(tx.id),
    )
    await callback.answer()


@router_receipts.callback_query(TxCategory.filter())
async def tx_set_category(callback: CallbackQuery, callback_data: TxCategory):
    """Меняет категорию и запоминает выбор пользователя."""
    user_id = callback.from_user.id
    tx = await get_transaction(user_id, callback_data.tx_id)
    if tx is None:
        await callback.answer(TX_NOT_FOUND, show_alert=True)
        return
    category = CATEGORY_NAMES[callback_data.index]
    await set_transaction_category(user_id, tx.id, category)
    await show_transaction(callback.message, user_id, tx.id)
    await callback.answer(
        CATEGORY_SAVED.format(merchant=tx.merchant, category=category)
    )


@router_receipts.callback_query(TxAction.filter(F.action == "card"))
async def tx_card_menu(callback: CallbackQuery, callback_data: TxAction):
    """Показывает выбор карты."""
    cards = await get_cards(callback.from_user.id)
    if not cards:
        await callback.answer(NO_CARDS_FOR_TX, show_alert=True)
        return
    await callback.message.edit_text(
        CHOOSE_CARD,
        reply_markup=tx_cards_kb(callback_data.tx_id, cards),
    )
    await callback.answer()


@router_receipts.callback_query(TxCard.filter())
async def tx_set_card(callback: CallbackQuery, callback_data: TxCard):
    """Привязывает операцию к карте."""
    user_id = callback.from_user.id
    await update_transaction(
        user_id,
        callback_data.tx_id,
        card_id=callback_data.card_id or None,
    )
    await show_transaction(callback.message, user_id, callback_data.tx_id)
    await callback.answer()


@router_receipts.callback_query(TxAction.filter(F.action == "count"))
async def tx_count(callback: CallbackQuery, callback_data: TxAction):
    """Учитывает перевод как обычный расход."""
    user_id = callback.from_user.id
    await update_transaction(user_id, callback_data.tx_id, is_transfer=False)
    await show_transaction(callback.message, user_id, callback_data.tx_id)
    await callback.answer()


@router_receipts.callback_query(TxAction.filter(F.action == "delete"))
async def tx_delete(callback: CallbackQuery, callback_data: TxAction):
    """Удаляет операцию."""
    await delete_transaction(callback.from_user.id, callback_data.tx_id)
    await callback.message.edit_text(TX_DELETED)
    await callback.answer()
