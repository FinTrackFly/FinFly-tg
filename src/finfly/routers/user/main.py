from aiogram import Dispatcher

from finfly.routers.user.basic_cmd import router_basic_cmd


def register_user_handlers(dp: Dispatcher) -> None:
    """Регистрирует все обработчики для пользователя."""
    for router in (router_basic_cmd,):
        dp.include_router(router)
