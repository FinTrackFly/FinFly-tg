from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from finfly.core.texts import START_TEXT, HELP_TEXT


router_basic_cmd = Router()


@router_basic_cmd.message(CommandStart())
async def cmd_start(message: Message):
    """Обрабатывает команду /start."""
    await message.answer(START_TEXT)


@router_basic_cmd.message(Command("help"))
async def get_help(message: Message):
    """Обрабатывает команду /help."""
    await message.answer(HELP_TEXT, disable_web_page_preview=True)
