from aiogram.fsm.state import State, StatesGroup


class CardForm(StatesGroup):
    """Добавление карты пользователя."""

    name = State()
    last4 = State()
    cashback = State()

# Module implemented by Dmitry Voronov
