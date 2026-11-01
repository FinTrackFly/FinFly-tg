from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from finfly.core.constants import STATS_PERIODS
from finfly.core.keyboards import StatsPeriod, stats_period_kb
from finfly.core.texts import BTN_STATS
from finfly.core.utils import utc_now
from finfly.db.requests import get_cards, get_transactions_since
from finfly.services.stats import build_summary, format_metrics, format_summary

router_stats = Router()

DEFAULT_PERIOD_DAYS = 30


async def build_stats_text(user_id: int, days: int) -> str:
    """Сводка расходов пользователя за days дней (0 — всё время)."""
    since = utc_now() - timedelta(days=days) if days else None
    summary = build_summary(await get_transactions_since(user_id, since))
    return format_summary(
        summary,
        await get_cards(user_id),
        STATS_PERIODS[days],
    )


@router_stats.message(Command("stats"))
@router_stats.message(F.text == BTN_STATS)
async def cmd_stats(message: Message):
    """Показывает сводку расходов по категориям и картам."""
    await message.answer(
        await build_stats_text(message.from_user.id, DEFAULT_PERIOD_DAYS),
        reply_markup=stats_period_kb(DEFAULT_PERIOD_DAYS),
    )


@router_stats.callback_query(StatsPeriod.filter())
async def stats_period(callback: CallbackQuery, callback_data: StatsPeriod):
    """Переключает период сводки."""
    days = callback_data.days
    if days not in STATS_PERIODS:
        await callback.answer()
        return
    try:
        await callback.message.edit_text(
            await build_stats_text(callback.from_user.id, days),
            reply_markup=stats_period_kb(days),
        )
    except TelegramBadRequest:
        pass  # Сообщение не изменилось
    await callback.answer()


@router_stats.message(Command("metrics"))
async def cmd_metrics(message: Message):
    """Показывает метрики качества обработки чеков."""
    transactions = await get_transactions_since(message.from_user.id)
    await message.answer(format_metrics(transactions))
