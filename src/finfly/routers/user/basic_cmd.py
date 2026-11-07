from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from finfly.core.keyboards import main_menu_kb
from finfly.core.texts import (
    BTN_HELP,
    CANCEL_TEXT,
    HELP_TEXT,
    NOTHING_TO_CANCEL,
    START_TEXT,
)

router_basic_cmd = Router()


@router_basic_cmd.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обрабатывает команду /start."""
    await state.clear()
    await message.answer(START_TEXT, reply_markup=main_menu_kb())


@router_basic_cmd.message(Command("help"))
@router_basic_cmd.message(F.text == BTN_HELP)
async def get_help(message: Message):
    """Обрабатывает команду /help."""
    await message.answer(
        HELP_TEXT,
        reply_markup=main_menu_kb(),
        disable_web_page_preview=True,
    )


@router_basic_cmd.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Обрабатывает команду /cancel — сбрасывает текущее действие."""
    if await state.get_state() is None:
        await message.answer(NOTHING_TO_CANCEL, reply_markup=main_menu_kb())
        return
    await state.clear()
    await message.answer(CANCEL_TEXT, reply_markup=main_menu_kb())
