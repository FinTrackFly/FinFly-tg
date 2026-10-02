from aiogram import Dispatcher

from finfly.routers.user import register_user_handlers


def register_all_handlers(dp: Dispatcher):
    register_user_handlers(dp)
