import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums.parse_mode import ParseMode

from finfly.core.config import BOT_TOKEN, DEBUG, TG_PROXY
from finfly.db.engine import init_db
from finfly.routers import register_all_handlers


async def main():
    logging.basicConfig(level=logging.INFO if DEBUG else logging.WARNING)
    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        session=AiohttpSession(proxy=TG_PROXY),
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dispatcher = Dispatcher()

    register_all_handlers(dispatcher)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(">>> Exit.")
