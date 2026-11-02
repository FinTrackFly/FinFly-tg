"""Рекомендации: ожидание, отмена, темы, настройки и обратная связь."""

from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender
from aiogram.utils.keyboard import InlineKeyboardBuilder

from finfly.core.texts import (
    BTN_STATS,
    BTN_ADVICE,
    BTN_CARDS,
    BTN_HISTORY,
    BTN_HELP,
)
from finfly.db.requests import get_transactions_since
from finfly.services.agents.recommender import (
    AdviceResult,
    GOALS,
    generate_advice,
)
from finfly.services.stats import (
    get_options,
    positive_int,
    owned,
    set_option,
    split_text,
    stable_key,
)

router_advice = Router()
logger = logging.getLogger(__name__)
pending_advice: dict[int, tuple[asyncio.Task, str]] = {}
TOTAL_TIMEOUT = 30
PREFERENCE_PAGE_SIZE = 12


class AdviceAction(CallbackData, prefix="adv"):
    owner: int
    action: str
    value: str = ""


class AdviceInput(StatesGroup):
    preference = State()
    question = State()


def keyboard(user_id, buttons, columns=2):
    builder = InlineKeyboardBuilder()
    for label, action, value in buttons:
        builder.button(
            text=label if len(label) <= 60 else label[:59] + "…",
            callback_data=AdviceAction(
                owner=user_id, action=action, value=str(value)
            ),
        )
    builder.adjust(columns)
    return builder.as_markup()


def result_menu(user_id, result):
    if not result.fingerprint:
        return keyboard(
            user_id, [("Обновить", "retry", ""), ("Выбрать цель", "goals", "")]
        )
    token = result.fingerprint[:16]
    return keyboard(
        user_id,
        [
            ("Кратко", "short", token),
            ("Подробнее", "long", token),
            ("Почему такой совет?", "why", token),
            ("Задать вопрос", "ask", ""),
            ("Полезно 👍", "up", token),
            ("Не полезно 👎", "down", token),
            ("Обновить", "retry", ""),
            ("Выбрать цель", "goals", ""),
            ("Мои предпочтения", "preferences", ""),
        ],
    )


async def send_text(message, text, reply_markup=None):
    parts = split_text(text)
    for index, part in enumerate(parts):
        await message.answer(
            part,
            parse_mode=None,
            reply_markup=reply_markup if index == len(parts) - 1 else None,
        )


async def produce_advice(message, user_id, token, *, force=False, question=""):
    reply = None
    try:
        reply = await message.answer(
            "Готовлю рекомендации…",
            parse_mode=None,
            reply_markup=keyboard(user_id, [("Отменить", "cancel", token)], 1),
        )
        async with ChatActionSender.typing(
            bot=message.bot, chat_id=message.chat.id
        ):
            result = await asyncio.wait_for(
                generate_advice(user_id, force=force, question=question),
                timeout=TOTAL_TIMEOUT,
            )
        if result is None:
            await reply.edit_text(
                "За последние 30 дней нет расходов. Добавь покупки и повтори"
                " запрос.",
                parse_mode=None,
                reply_markup=keyboard(
                    user_id, [("Выбрать цель", "goals", "")], 1
                ),
            )
            return
        options = await get_options(user_id)
        text = result.render(
            detailed=options.get("advice_detail") == "long" or bool(question)
        )
        if result.cached:
            text = (
                "Данные не изменились — показываю сохранённую"
                " рекомендацию.\n\n" + text
            )
        parts = split_text(text)
        # Для уточняющего ответа не показываем оценку/кнопки от другого отчёта.
        markup = (
            result_menu(user_id, result)
            if not question
            else keyboard(
                user_id,
                [
                    ("Ещё вопрос", "ask", ""),
                    ("Обновить рекомендации", "retry", ""),
                ],
            )
        )
        await reply.edit_text(
            parts[0],
            parse_mode=None,
            reply_markup=markup if len(parts) == 1 else None,
        )
        for index, part in enumerate(parts[1:], 1):
            await message.answer(
                part,
                parse_mode=None,
                reply_markup=markup if index == len(parts) - 1 else None,
            )
    except asyncio.CancelledError:
        if reply is not None:
            await reply.edit_text(
                "Подготовка рекомендации отменена.",
                parse_mode=None,
                reply_markup=None,
            )
        raise
    except asyncio.TimeoutError:
        if reply is not None:
            await reply.edit_text(
                "Время ожидания истекло. Можно повторить запрос.",
                parse_mode=None,
                reply_markup=keyboard(
                    user_id, [("Повторить", "retry", "")], 1
                ),
            )
    except Exception:
        logger.exception("Не удалось отправить рекомендацию")
        if reply is not None:
            await reply.edit_text(
                "Не удалось подготовить ответ. Попробуй ещё раз.",
                parse_mode=None,
                reply_markup=keyboard(
                    user_id, [("Повторить", "retry", "")], 1
                ),
            )


async def run_advice(
    message, user_id, *, force=False, question="", state=None
):
    active = pending_advice.get(user_id)
    if active is not None and not active[0].done():
        await send_text(
            message,
            "Рекомендация уже готовится. Дождись ответа или отмени запрос."
            " Если отправлял вопрос, повтори его после завершения.",
            keyboard(user_id, [("Отменить", "cancel", active[1])], 1),
        )
        return False
    token = secrets.token_hex(4)
    task = asyncio.create_task(
        produce_advice(message, user_id, token, force=force, question=question)
    )
    # До первого await запрос уже зарегистрирован, второй не запустится.
    pending_advice[user_id] = (task, token)
    try:
        if state is not None:
            await state.clear()
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if pending_advice.get(user_id, (None,))[0] is task:
            pending_advice.pop(user_id, None)
    return True


@router_advice.message(Command("advice"))
@router_advice.message(F.text == BTN_ADVICE)
async def cmd_advice(message: Message, state: FSMContext):
    await state.clear()
    await run_advice(message, message.from_user.id)


@router_advice.message(Command("cancel_advice"))
async def cancel_advice(message: Message):
    active = pending_advice.get(message.from_user.id)
    if active and not active[0].done():
        active[0].cancel()
    else:
        await send_text(message, "Сейчас нет запроса, который нужно отменить.")


async def show_preferences(message, user_id, page=0):
    options = await get_options(user_id)
    transactions = owned(await get_transactions_since(user_id), user_id)
    protected = set(options.get("protected_categories", []))
    text = "Пожелания: " + (options.get("preferences") or "не заданы")
    text += "\nНе предлагать экономить на: " + (
        ", ".join(sorted(protected)) or "не выбрано"
    )
    text += (
        "\nНажми категорию, чтобы включить или убрать защиту. Важные подписки"
        " отмечаются в /subscriptions."
    )
    categories = sorted({tx.category for tx in transactions} | protected)
    pages = max(
        1, (len(categories) + PREFERENCE_PAGE_SIZE - 1) // PREFERENCE_PAGE_SIZE
    )
    page = min(positive_int(page, "Неверный номер страницы."), pages - 1)
    buttons = [
        ("Изменить пожелания", "editprefs", ""),
        ("Очистить пожелания", "clearprefs", ""),
    ]
    buttons += [
        (
            ("✓ " if category in protected else "") + category,
            "protect",
            stable_key(category),
        )
        for category in categories[
            page * PREFERENCE_PAGE_SIZE : (page + 1) * PREFERENCE_PAGE_SIZE
        ]
    ]
    if page:
        buttons.append(("← Назад", "prefpage", page - 1))
    if page + 1 < pages:
        buttons.append(("Далее →", "prefpage", page + 1))
    buttons.append(("К рекомендациям", "retry", ""))
    await send_text(
        message,
        text + f"\nКатегории · страница {page + 1}/{pages}",
        keyboard(user_id, buttons),
    )


@router_advice.callback_query(AdviceAction.filter())
async def advice_action(
    callback: CallbackQuery, callback_data: AdviceAction, state: FSMContext
):
    if callback_data.owner != callback.from_user.id:
        await callback.answer(
            "Эта кнопка принадлежит другому пользователю.", show_alert=True
        )
        return
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    user_id, action, value = (
        callback.from_user.id,
        callback_data.action,
        callback_data.value,
    )
    message = callback.message
    if action == "cancel":
        active = pending_advice.get(user_id)
        if active and active[1] == value and not active[0].done():
            active[0].cancel()
        else:
            await send_text(message, "Этот запрос уже завершён.")
        return
    await state.clear()
    if action == "retry":
        await run_advice(message, user_id, force=True)
        return
    options = await get_options(user_id)
    if action == "goals":
        await send_text(
            message,
            "Что разобрать?",
            keyboard(
                user_id,
                [(label, "goal", key) for key, label in GOALS.items()],
                1,
            ),
        )
    elif action == "goal":
        if value not in GOALS:
            return
        await set_option(user_id, "goal", value)
        await set_option(user_id, "last_advice", None)
        await run_advice(message, user_id, force=True)
    elif action in {"preferences", "prefpage"}:
        try:
            await show_preferences(
                message, user_id, value if action == "prefpage" else 0
            )
        except ValueError as error:
            await send_text(message, str(error))
    elif action == "protect":
        transactions = owned(await get_transactions_since(user_id), user_id)
        categories = {
            stable_key(category): category
            for category in {tx.category for tx in transactions}
            | set(options.get("protected_categories", []))
        }
        if value not in categories:
            await send_text(message, "Категория больше не найдена.")
            return
        protected = set(options.get("protected_categories", []))
        category = categories[value]
        if category in protected:
            protected.remove(category)
        else:
            protected.add(category)
        await set_option(user_id, "protected_categories", sorted(protected))
        await set_option(user_id, "last_advice", None)
        await show_preferences(message, user_id)
    elif action == "editprefs":
        await state.set_state(AdviceInput.preference)
        await send_text(
            message,
            "Напиши пожелания к советам (до 1000 символов). Для строгого"
            " запрета экономить на категории используй её кнопку. Отмена:"
            " /analysis_cancel",
        )
    elif action == "clearprefs":
        await set_option(user_id, "preferences", "")
        await set_option(user_id, "last_advice", None)
        await show_preferences(message, user_id)
    elif action == "ask":
        await state.set_state(AdviceInput.question)
        await send_text(
            message,
            "Задай вопрос о своих расходах. Например: «Что изменилось в"
            " расходах на продукты?» Отмена: /analysis_cancel",
        )
    else:
        result = AdviceResult.from_saved(options.get("last_advice"))
        if result is None or result.fingerprint[:16] != value:
            await send_text(
                message, "Рекомендации обновились. Открой /advice заново."
            )
            return
        if action in {"short", "long"}:
            await set_option(user_id, "advice_detail", action)
            await send_text(
                message,
                result.render(detailed=action == "long"),
                result_menu(user_id, result),
            )
        elif action == "why":
            await send_text(message, result.explanation())
        elif action in {"up", "down"}:
            await set_option(
                user_id,
                "feedback:last",
                {"fingerprint": result.fingerprint, "rating": action},
            )
            await send_text(message, "Оценка сохранена. Спасибо!")


@router_advice.message(Command("preferences"))
async def preference_command(
    message: Message, command: CommandObject, state: FSMContext
):
    await state.clear()
    if command.args:
        if len(command.args) > 1000:
            await send_text(message, "Сократи пожелания до 1000 символов.")
            return
        await set_option(
            message.from_user.id, "preferences", command.args.strip()
        )
        await set_option(message.from_user.id, "last_advice", None)
    await show_preferences(message, message.from_user.id)


@router_advice.message(
    AdviceInput.preference,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_({BTN_STATS, BTN_ADVICE, BTN_CARDS, BTN_HISTORY, BTN_HELP}),
)
async def preference_input(message: Message, state: FSMContext):
    if len(message.text.strip()) > 1000:
        await send_text(message, "Сократи пожелания до 1000 символов.")
        return
    await set_option(message.from_user.id, "preferences", message.text.strip())
    await set_option(message.from_user.id, "last_advice", None)
    await state.clear()
    await show_preferences(message, message.from_user.id)


@router_advice.message(Command("ask"))
async def ask_command(
    message: Message, command: CommandObject, state: FSMContext
):
    if not command.args:
        await state.set_state(AdviceInput.question)
        await send_text(
            message, "Напиши вопрос о своих расходах. Отмена: /analysis_cancel"
        )
        return
    if len(command.args) > 1000:
        await send_text(message, "Вопрос должен содержать до 1000 символов.")
        return
    await run_advice(
        message,
        message.from_user.id,
        force=True,
        question=command.args,
        state=state,
    )


@router_advice.message(
    AdviceInput.question,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_({BTN_STATS, BTN_ADVICE, BTN_CARDS, BTN_HISTORY, BTN_HELP}),
)
async def question_input(message: Message, state: FSMContext):
    if len(message.text) > 1000:
        await send_text(message, "Сократи вопрос до 1000 символов.")
        return
    await run_advice(
        message,
        message.from_user.id,
        force=True,
        question=message.text,
        state=state,
    )
