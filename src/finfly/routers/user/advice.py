import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from finfly.core.constants import ADVICE_PERIOD_DAYS
from finfly.core.texts import (
    ADVICE_EMPTY,
    ADVICE_ERROR,
    ADVICE_PROCESSING,
    ADVICE_TITLE,
    BTN_ADVICE,
)
from finfly.core.utils import escape_md
from finfly.services.agents.recommender import recommendations_agent

router_advice = Router()
logger = logging.getLogger(__name__)


@router_advice.message(Command("advice"))
@router_advice.message(F.text == BTN_ADVICE)
async def cmd_advice(message: Message):
    """Показывает персональные рекомендации по тратам и кэшбэку."""
    reply = await message.answer(ADVICE_PROCESSING)
    try:
        async with ChatActionSender.typing(
            bot=message.bot,
            chat_id=message.chat.id,
        ):
            advice = await recommendations_agent(message.from_user.id)
    except Exception:
        logger.exception("Ошибка Recommendations-агента")
        await reply.edit_text(ADVICE_ERROR)
        return

    if advice is None:
        await reply.edit_text(ADVICE_EMPTY)
        return
    await reply.edit_text(
        ADVICE_TITLE.format(days=ADVICE_PERIOD_DAYS) + escape_md(advice)
    )
