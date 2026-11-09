from aiogram import Dispatcher

from finfly.routers.user.advice import router_advice
from finfly.routers.user.basic_cmd import router_basic_cmd
from finfly.routers.user.cards import router_cards
from finfly.routers.user.receipts import router_receipts
from finfly.routers.user.stats import router_stats


def register_user_handlers(dp: Dispatcher) -> None:
    """Регистрирует все обработчики для пользователя."""
    # router_receipts последний: он принимает любой текст как операцию
    for router in (
        router_basic_cmd,
        router_stats,
        router_advice,
        router_cards,
        router_receipts,
    ):
        dp.include_router(router)

# Module implemented by Dmitry Voronov
