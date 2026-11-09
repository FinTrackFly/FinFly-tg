from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from finfly.core.keyboards import (
    SKIP_CALLBACK,
    CardAction,
    cards_kb,
    main_menu_kb,
    skip_kb,
)
from finfly.core.state import CardForm
from finfly.core.texts import (
    BTN_CARDS,
    CARD_ADDED,
    CARD_ASK_CASHBACK,
    CARD_ASK_LAST4,
    CARD_ASK_NAME,
    CARD_BAD_LAST4,
    CARD_DELETED,
    CARD_LINE,
    CARD_NAME_TOO_LONG,
    CARD_NO_CASHBACK,
    CARDS_EMPTY,
    CARDS_TITLE,
)
from finfly.core.utils import bold, escape_md
from finfly.db.requests import add_card, delete_card, get_cards

router_cards = Router()

MAX_CARD_NAME_LENGTH = 64


async def build_cards_text(user_id: int) -> tuple[str, list]:
    """Список карт пользователя и сами карты."""
    cards = await get_cards(user_id)
    if not cards:
        return CARDS_EMPTY, cards
    lines = [
        f"{number}. "
        + CARD_LINE.format(
            name=bold(card.name),
            last4=f" ·· {card.last4}" if card.last4 else "",
            cashback=escape_md(card.cashback) or CARD_NO_CASHBACK,
        )
        for number, card in enumerate(cards, start=1)
    ]
    return CARDS_TITLE + "\n\n".join(lines), cards


async def send_cards(message: Message, user_id: int):
    """Отправляет список карт с кнопками управления."""
    text, cards = await build_cards_text(user_id)
    await message.answer(text, reply_markup=cards_kb(cards))


@router_cards.message(Command("cards"))
@router_cards.message(F.text == BTN_CARDS)
async def cmd_cards(message: Message):
    """Показывает карты пользователя."""
    await send_cards(message, message.from_user.id)


@router_cards.callback_query(CardAction.filter(F.action == "delete"))
async def card_delete(callback: CallbackQuery, callback_data: CardAction):
    """Удаляет карту."""
    user_id = callback.from_user.id
    await delete_card(user_id, callback_data.card_id)
    text, cards = await build_cards_text(user_id)
    await callback.message.edit_text(text, reply_markup=cards_kb(cards))
    await callback.answer(CARD_DELETED)


###
### Добавление карты
###


@router_cards.callback_query(CardAction.filter(F.action == "add"))
async def card_add(callback: CallbackQuery, state: FSMContext):
    """Начинает добавление карты."""
    await state.set_state(CardForm.name)
    await callback.message.answer(CARD_ASK_NAME)
    await callback.answer()


@router_cards.message(CardForm.name, F.text)
async def card_name(message: Message, state: FSMContext):
    """Принимает название карты."""
    name = message.text.strip()
    if len(name) > MAX_CARD_NAME_LENGTH:
        await message.answer(CARD_NAME_TOO_LONG)
        return
    await state.update_data(name=name)
    await state.set_state(CardForm.last4)
    await message.answer(CARD_ASK_LAST4, reply_markup=skip_kb())


@router_cards.message(CardForm.last4, F.text)
async def card_last4(message: Message, state: FSMContext):
    """Принимает последние 4 цифры карты."""
    last4 = message.text.strip()
    if not (len(last4) == 4 and last4.isdigit()):
        await message.answer(CARD_BAD_LAST4, reply_markup=skip_kb())
        return
    await state.update_data(last4=last4)
    await state.set_state(CardForm.cashback)
    await message.answer(CARD_ASK_CASHBACK, reply_markup=skip_kb())


@router_cards.callback_query(CardForm.last4, F.data == SKIP_CALLBACK)
async def card_skip_last4(callback: CallbackQuery, state: FSMContext):
    """Пропускает ввод цифр карты."""
    await state.update_data(last4=None)
    await state.set_state(CardForm.cashback)
    await callback.message.edit_reply_markup()
    await callback.message.answer(CARD_ASK_CASHBACK, reply_markup=skip_kb())
    await callback.answer()


async def finish_card(
    message: Message,
    state: FSMContext,
    user_id: int,
    cashback: str,
):
    """Сохраняет карту и показывает обновлённый список."""
    data = await state.get_data()
    await state.clear()
    await add_card(user_id, data["name"], data.get("last4"), cashback)
    await message.answer(
        CARD_ADDED.format(name=bold(data["name"])),
        reply_markup=main_menu_kb(),
    )
    await send_cards(message, user_id)


@router_cards.message(CardForm.cashback, F.text)
async def card_cashback(message: Message, state: FSMContext):
    """Принимает условия кэшбэка."""
    await finish_card(
        message,
        state,
        message.from_user.id,
        message.text.strip(),
    )


@router_cards.callback_query(CardForm.cashback, F.data == SKIP_CALLBACK)
async def card_skip_cashback(callback: CallbackQuery, state: FSMContext):
    """Пропускает ввод условий кэшбэка."""
    await callback.message.edit_reply_markup()
    await finish_card(callback.message, state, callback.from_user.id, "")
    await callback.answer()

# Module implemented by Dmitry Voronov
