"""Интерфейс аналитики: данные и кнопки привязаны к владельцу."""

from __future__ import annotations


from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from finfly.core.keyboards import StatsPeriod
from finfly.core.texts import (
    BTN_STATS,
    BTN_ADVICE,
    BTN_CARDS,
    BTN_HISTORY,
    BTN_HELP,
)
from datetime import timedelta
from finfly.db.requests import get_cards
from finfly.services.stats import (
    PERIODS,
    allow_action,
    load_transactions,
    local_today,
    read_view,
    user_timezone,
    positive_int,
    is_financial,
    signed_amount,
    amounts_text,
    build_summary,
    chart_svg,
    csv_bytes,
    currency_groups,
    detect_anomalies,
    detect_subscriptions,
    display_money,
    forecast_month,
    format_comparison,
    format_metrics,
    format_summary,
    get_options,
    is_expense,
    money,
    owned,
    parse_cashback_rule,
    period_bounds,
    period_title,
    select_transactions,
    set_option,
    split_text,
    stable_key,
    time_series,
    transaction_date,
    xlsx_bytes,
)

router_stats = Router()
PAGE_SIZE = 8
FILTER_PAGE_SIZE = 12
EXPORT_LIMIT = 20_000
BACK = ("← В меню", "menu", "")

HELP = """Аналитика FinFly
/timezone — часовой пояс для времени добавления операций
/stats — сводка, периоды, фильтры и меню
/compare — сравнение с предыдущим периодом
/daily, /weekly — динамика по дням и неделям
/top — крупнейшие покупки по валютам
/merchants — магазины по сумме
/details — список покупок с переключением страниц
/search магазин — поиск в выбранном периоде
/chart, /categories — изображения диаграмм SVG
/forecast — оценка расходов к концу месяца
/export — CSV или Excel с выбранными покупками
/security — необычные операции
/subscriptions — вероятные подписки и отметка «важна»
/cashback — ставки и лимиты твоих карт
/advice — рекомендации, тема, краткость, объяснения
/ask вопрос — уточнение по имеющимся фактам
/preferences — пожелания и категории, на которых не экономить
/cancel_advice — остановить подготовку рекомендации
/analysis_cancel — отменить ввод даты/фильтра/условий
/metrics — показатели обработки операций

Сигналы основаны только на добавленных операциях.
Бот не получает списания из банка автоматически и не отменяет подписки сам."""


class AnalysisAction(CallbackData, prefix="an"):
    owner: int
    action: str
    value: str = ""


class AnalysisInput(StatesGroup):
    dates = State()
    merchant = State()
    cashback = State()
    timezone = State()


class LeaveAnalysisInput(BaseMiddleware):
    """Переход к картам/истории завершает только наш ввод, не FSM команды."""

    async def __call__(self, handler, event, data):
        text = event.text or ""
        command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
        if text in {BTN_CARDS, BTN_HISTORY} or command in {
            "/cards",
            "/history",
        }:
            state = data.get("state")
            if state is not None:
                current = await state.get_state() or ""
                if current.startswith(("AnalysisInput:", "AdviceInput:")):
                    await state.clear()
        return await handler(event, data)


router_stats.message.outer_middleware(LeaveAnalysisInput())


def keyboard(user_id: int, buttons: list[tuple], columns=2):
    builder = InlineKeyboardBuilder()
    for label, action, value in buttons:
        builder.button(
            text=label if len(label) <= 60 else label[:59] + "…",
            callback_data=AnalysisAction(
                owner=user_id, action=action, value=str(value)
            ),
        )
    builder.adjust(columns)
    return builder.as_markup()


def menu(user_id):
    return keyboard(
        user_id,
        [
            ("Период", "periods", ""),
            ("Фильтры", "filters", ""),
            ("Сравнить", "compare", ""),
            ("Покупки", "details", 0),
            ("По дням", "daily", ""),
            ("По неделям", "weekly", ""),
            ("График", "chart", ""),
            ("Категории: диаграмма", "categories", ""),
            ("Крупнейшие покупки", "top", ""),
            ("Магазины", "merchants", ""),
            ("Прогноз месяца", "forecast", ""),
            ("Экспорт", "export", ""),
            ("Проверить операции", "security", ""),
            ("Подписки", "subscriptions", ""),
            ("Условия кэшбэка", "cashback", ""),
            ("Часовой пояс", "timezone", ""),
            ("Все команды", "help", ""),
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


async def data_for(user_id, view=None, purpose=""):
    tz = user_timezone((view or {}).get("timezone", "+00:00"))
    today = local_today(tz)
    start, end = (None, today + timedelta(days=1))
    if purpose == "subscriptions":
        start = today - timedelta(days=800)
    elif purpose == "security":
        start = today - timedelta(days=210)
    elif purpose == "forecast":
        start = today.replace(day=1)
    elif view is not None:
        start, end = period_bounds(view, today)
        if purpose == "compare" and start is not None:
            start -= end - start
    transactions = owned(
        await load_transactions(user_id, start=start, end=end, tz=tz), user_id
    )
    cards = [
        card for card in await get_cards(user_id) if card.user_id == user_id
    ]
    return transactions, cards


async def show_stats(message, user_id):
    options = await get_options(user_id)
    view = read_view(options)
    transactions, cards = await data_for(user_id, view)
    selected = select_transactions(transactions, view, user_id=user_id)
    text = format_summary(build_summary(selected), cards, period_title(view))
    active = []
    for name, label in (
        ("category", "Категория"),
        ("currency", "Валюта"),
        ("merchant", "Магазин"),
    ):
        if view.get(name):
            active.append(f"{label}: {view[name]}")
    if "card_id" in view:
        names = {card.id: card.name for card in cards}
        active.append("Карта: " + names.get(view["card_id"], "Без карты"))
    if active:
        text += "\n\nФильтры: " + "; ".join(active)
    text += "\n\nУчёт по дате покупки; если её нет — по времени добавления."
    text += f" Часовой пояс: UTC{view['timezone']} (изменить: /timezone)."
    markup = menu(user_id)
    clear_buttons = [
        ("✕ " + label, "unfilter", key)
        for key, label in (
            ("category", "Категория"),
            ("currency", "Валюта"),
            ("card_id", "Карта"),
            ("merchant", "Магазин"),
        )
        if key in view
    ]
    if clear_buttons:
        markup.inline_keyboard += keyboard(
            user_id, clear_buttons
        ).inline_keyboard
    await send_text(message, text, markup)


async def build_stats_text(user_id: int, days: int) -> str:
    transactions, cards = await data_for(user_id)
    view = {"period": "all" if days == 0 else str(days)}
    return format_summary(
        build_summary(
            select_transactions(transactions, view, user_id=user_id)
        ),
        cards,
        period_title(view),
    )


@router_stats.message(Command("stats"))
@router_stats.message(F.text == BTN_STATS)
async def cmd_stats(message: Message, state: FSMContext):
    await state.clear()
    await show_stats(message, message.from_user.id)


@router_stats.callback_query(StatsPeriod.filter())
async def legacy_period(
    callback: CallbackQuery, callback_data: StatsPeriod, state: FSMContext
):
    await callback.answer()
    # Кнопки старого интерфейса допустимы только в личном чате владельца.
    if (
        not isinstance(callback.message, Message)
        or callback.message.chat.id != callback.from_user.id
    ):
        return
    if callback_data.days not in {0, 7, 30}:
        return
    await state.clear()
    await set_option(
        callback.from_user.id,
        "stats_view",
        {
            "period": (
                "all" if callback_data.days == 0 else str(callback_data.days)
            )
        },
    )
    await show_stats(callback.message, callback.from_user.id)


async def show_subscriptions(
    message, user_id, transactions, options, subscriptions=None
):
    if subscriptions is None:
        subscriptions = detect_subscriptions(
            transactions, tz=user_timezone(options.get("timezone", "+00:00"))
        )
    await send_text(
        message,
        "Вероятные подписки по регулярным списаниям. Это совпадения в истории,"
        " а не подтверждение договора. После одного платежа подписку"
        " определить нельзя.",
    )
    if not subscriptions:
        await send_text(
            message,
            "Регулярных списаний пока не обнаружено. Для месячной подписки"
            " нужны хотя бы два похожих платежа в разные месяцы.",
        )
    for sub in subscriptions:
        status = options.get("subscription:" + sub["key"], "review")
        label = {
            "important": "Важная — не предлагать отмену",
            "dismissed": "Отмечена как не подписка",
            "review": "Проверь, нужна ли эта подписка",
        }.get(status, "Проверь, нужна ли эта подписка")
        text = (
            f"{sub['merchant']}\n"
            f"{display_money(sub['amount'], sub['currency'])},"
            f" {sub['cadence']}\nПовторений: {sub['count']}; последнее"
            f" списание: {sub['last']}\nОжидаемая следующая дата:"
            f" {sub['next']} (оценка)\n{label}"
        )
        if sub["trial"]:
            text += (
                "\nПеред серией найден небольшой платёж: возможно, пробный"
                " период сменился подпиской."
            )
        if sub["date_estimated"]:
            text += (
                "\nНекоторые даты взяты из времени загрузки, поэтому ритм"
                " может быть неточным."
            )
        if status == "review":
            buttons = [
                ("Важна — оставить", "important", sub["key"]),
                ("Это не подписка", "dismiss", sub["key"]),
            ]
        else:
            buttons = [("Вернуть на проверку", "review", sub["key"])]
        await send_text(message, text, keyboard(user_id, buttons, 1))
    await send_text(
        message, "Раздел анализа расходов", keyboard(user_id, [BACK], 1)
    )


async def show_security(message, user_id, transactions, options):
    acknowledged = [
        int(key.split(":", 1)[1])
        for key, value in options.items()
        if key.startswith("ack:")
        and key.split(":", 1)[1].isascii()
        and key.split(":", 1)[1].isdecimal()
        and value
    ]
    alerts = detect_anomalies(
        transactions,
        acknowledged=acknowledged,
        tz=user_timezone(options.get("timezone", "+00:00")),
    )
    await send_text(
        message,
        "Проверка операций за последние 30 дней. Сигнал означает отличие от"
        " доступной истории, а не доказательство мошенничества. Проверь"
        " покупку сам; если не узнаёшь её — свяжись с банком.",
    )
    if not alerts:
        await send_text(
            message,
            "Новых сигналов нет. При короткой истории сравнение ограничено;"
            " отсутствие сигналов не подтверждает безопасность всех операций.",
        )
    for alert in alerts:
        text = (
            f"{alert['date']} ·"
            f" {alert['merchant']}\n"
            f"{display_money(alert['amount'], alert['currency'])}\n"
            + "\n".join(alert["reasons"])
        )
        await send_text(
            message,
            text,
            keyboard(user_id, [("Это моя покупка", "ack", alert["id"])], 1),
        )
    await send_text(
        message,
        "Регулярные списания проверяются отдельно в списке подписок.",
        keyboard(
            user_id, [("Проверить подписки", "subscriptions", ""), BACK], 1
        ),
    )


def build_top_text(transactions, tz=None) -> str:
    tz = tz or user_timezone("+00:00")
    currencies = sorted({tx.currency for tx in transactions if is_expense(tx)})
    lines = ["Крупнейшие покупки выбранного периода"]
    for currency in currencies:
        lines.append("\n" + currency)
        largest = sorted(
            [
                tx
                for tx in transactions
                if is_expense(tx) and tx.currency == currency
            ],
            key=lambda tx: (-money(tx.amount), tx.id),
        )[:5]
        for number, tx in enumerate(largest, 1):
            lines.append(
                f"{number}. {transaction_date(tx, tz):%d.%m.%Y} —"
                f" {tx.merchant}: {display_money(tx.amount, currency)}"
            )
    return (
        "\n".join(lines) if currencies else "Расходов за выбранный период нет."
    )


async def report(message, user_id, action, value=""):
    options = await get_options(user_id)
    view = read_view(options)
    tz = user_timezone(view["timezone"])
    transactions, cards = await data_for(
        user_id, view if action != "metrics" else None, action
    )
    selected = select_transactions(transactions, view, user_id=user_id)
    summary = build_summary(selected)
    back = keyboard(user_id, [BACK], 1)
    if action == "compare":
        previous = build_summary(
            select_transactions(
                transactions, view, user_id=user_id, previous=True
            )
        )
        await send_text(
            message,
            period_title(view) + "\n" + format_comparison(summary, previous),
            back,
        )
    elif action in {"daily", "weekly"}:
        groups = time_series(selected, weekly=action == "weekly", tz=tz)
        lines = [
            (
                "Расходы по неделям (с понедельника)"
                if action == "weekly"
                else "Расходы по дням"
            ),
            period_title(view),
        ]
        for currency, points in groups.items():
            lines += ["\n" + currency] + [
                f"{day:%d.%m.%Y}: {display_money(amount, currency)}"
                for day, amount in points.items()
            ]
        await send_text(
            message,
            "\n".join(lines) if groups else "Расходов за период нет.",
            back,
        )
    elif action == "top":
        await send_text(
            message,
            period_title(view) + "\n" + build_top_text(selected, tz),
            back,
        )
    elif action == "merchants":
        lines = ["Магазины по сумме расходов", period_title(view)]
        for currency in sorted(summary.totals):
            lines.append("\n" + currency)
            lines += [
                f"{name}: {display_money(amount, currency)}"
                for name, amount in currency_groups(
                    summary.by_merchant, currency
                )
            ]
        await send_text(
            message,
            "\n".join(lines) if summary.totals else "Расходов нет.",
            back,
        )
    elif action == "forecast":
        forecast = forecast_month(
            select_transactions(
                transactions, dict(view, period="month"), user_id=user_id
            ),
            local_today(tz),
            tz,
        )
        await send_text(
            message,
            "Оценка расходов к концу текущего месяца: "
            + amounts_text(forecast)
            + ".\nПо внесённым покупкам текущего месяца при сохранении темпа."
            " Разовые покупки и неполная история могут сильно изменить"
            " результат. Применены выбранные фильтры.",
            back,
        )
    elif action == "details":
        expenses = sorted(
            [tx for tx in selected if is_financial(tx)],
            key=lambda tx: (transaction_date(tx, tz), tx.id),
            reverse=True,
        )
        pages = max(1, (len(expenses) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(
            positive_int(
                value or "0",
                "Страница должна быть целым неотрицательным числом.",
            ),
            pages - 1,
        )
        lines = [
            f"Покупки и возвраты · страница {page + 1}/{pages}",
            period_title(view),
        ]
        for tx in expenses[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
            label = "Возврат · " if signed_amount(tx) < 0 else ""
            lines.append(
                f"{transaction_date(tx, tz):%d.%m.%Y} ·"
                f" {tx.merchant}\n{label}{tx.category} ·"
                f" {display_money(signed_amount(tx), tx.currency)}"
            )
        if not expenses:
            lines.append("По выбранным фильтрам операций нет.")
        buttons = []
        if page:
            buttons.append(("← Назад", "details", page - 1))
        if page + 1 < pages:
            buttons.append(("Далее →", "details", page + 1))
        await send_text(
            message, "\n\n".join(lines), keyboard(user_id, buttons + [BACK])
        )
    elif action in {"chart", "categories"}:
        groups = (
            time_series(selected, tz=tz)
            if action == "chart"
            else {
                currency: dict(currency_groups(summary.by_category, currency))
                for currency in summary.totals
            }
        )
        title = (
            "Расходы по датам"
            if action == "chart"
            else "Расходы по категориям"
        )
        if not groups:
            await send_text(
                message,
                "Для диаграммы нужны операции за выбранный период.",
                back,
            )
        for currency, values in groups.items():
            items = list(values.items())
            for offset in range(0, len(items), 60):
                picture = chart_svg(
                    dict(items[offset : offset + 60]),
                    currency,
                    title,
                    group_total=sum(values.values()),
                )
                await message.answer_document(
                    BufferedInputFile(
                        picture,
                        filename=f"{action}_{currency}_{offset // 60 + 1}.svg",
                    ),
                    caption=f"{title} · {currency} · {period_title(view)}",
                    parse_mode=None,
                    reply_markup=back,
                )
    elif action in {"csv", "xlsx"}:
        expenses = sorted(
            [tx for tx in selected if is_financial(tx)],
            key=lambda tx: (transaction_date(tx, tz), tx.id),
            reverse=True,
        )
        exported = expenses[:EXPORT_LIMIT]
        content = (
            csv_bytes(exported, cards, tz)
            if action == "csv"
            else xlsx_bytes(exported, cards, tz)
        )
        if len(content) > 45 * 1024 * 1024:
            raise ValueError(
                "Файл слишком большой. Выбери более короткий период."
            )
        caption = (
            f"{period_title(view)} · выгружено {len(exported)} из"
            f" {len(expenses)} операций"
        )
        if len(exported) < len(expenses):
            caption += (
                ". Взяты последние операции; сузь период для полной выгрузки."
            )
        await message.answer_document(
            BufferedInputFile(content, filename=f"finfly_expenses.{action}"),
            caption=caption,
            parse_mode=None,
            reply_markup=back,
        )
    elif action == "subscriptions":
        await show_subscriptions(message, user_id, transactions, options)
    elif action == "security":
        await show_security(message, user_id, transactions, options)
    elif action == "metrics":
        await send_text(message, format_metrics(transactions), back)
    else:
        raise ValueError("Неизвестное действие. Открой /stats заново.")


async def filters_menu(message, user_id, choice="", page=0):
    transactions, cards = await data_for(user_id)
    if choice == "category":
        buttons = [
            (name, "cat", stable_key(name))
            for name in sorted({tx.category for tx in transactions})
        ]
    elif choice == "currency":
        buttons = [
            (code, "currency", code)
            for code in sorted({tx.currency for tx in transactions})
        ]
    elif choice == "card":
        buttons = [(card.name, "card", card.id) for card in cards] + [
            ("Без карты", "card", "none")
        ]
    else:
        buttons = [
            ("Категория", "choose", "category"),
            ("Карта", "choose", "card"),
            ("Валюта", "choose", "currency"),
            ("Магазин", "search", ""),
            ("Сбросить фильтры", "reset", ""),
        ]
    pages = max(1, (len(buttons) + FILTER_PAGE_SIZE - 1) // FILTER_PAGE_SIZE)
    page = min(positive_int(page, "Неверный номер страницы."), pages - 1)
    visible = buttons[page * FILTER_PAGE_SIZE : (page + 1) * FILTER_PAGE_SIZE]
    if page:
        visible.append(("← Назад", "filterpage", f"{choice}|{page - 1}"))
    if page + 1 < pages:
        visible.append(("Далее →", "filterpage", f"{choice}|{page + 1}"))
    await send_text(
        message,
        f"Выбери фильтр · страница {page + 1}/{pages}",
        keyboard(user_id, visible + [BACK]),
    )


async def cashback_menu(message, user_id, state):
    cards = [c for c in await get_cards(user_id) if c.user_id == user_id]
    options = await get_options(user_id)
    if not cards:
        await send_text(message, "Сначала добавь карту через меню карт.")
        return
    lines = ["Твои карты:"] + [f"{card.id}: {card.name}" for card in cards]
    rules = options.get("cashback_rules", [])
    for rule in rules:
        lines.append(
            f"Карта {rule['card_id']}: {rule['currency']}, {rule['category']},"
            f" {rule['rate']}%, лимит"
            f" {rule['cap'] if rule['cap'] is not None else 'нет'}"
        )
    lines += [
        "",
        (
            "Введи правило: ID карты; валюта; категория или *; процент;"
            " месячный лимит кэшбэка или нет"
        ),
        (
            "Пример (можно скопировать и заменить значения):\n1; RUB;"
            " Продукты; 5; 1000"
        ),
        (
            "Лимит относится к этому правилу. * — общая ставка с общим лимитом"
            " для остальных категорий."
        ),
        (
            "Для сложных условий банка расчёт будет приближённым. Указывай"
            " только действующие условия."
        ),
        "Удалить правило: удалить; ID карты; валюта; категория",
        "Отмена: /analysis_cancel",
    ]
    await state.set_state(AnalysisInput.cashback)
    await send_text(message, "\n".join(lines))


@router_stats.callback_query(AnalysisAction.filter())
async def analysis_action(
    callback: CallbackQuery, callback_data: AnalysisAction, state: FSMContext
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
    if not allow_action("analysis", user_id, interval=0.25):
        await send_text(message, "Подожди немного и нажми кнопку ещё раз.")
        return
    await state.clear()
    try:
        options = await get_options(user_id)
        view = read_view(options)
        if action == "menu":
            await show_stats(message, user_id)
        elif action == "timezone":
            await timezone_prompt(message, state)
        elif action == "unfilter":
            if value not in {"category", "card_id", "currency", "merchant"}:
                raise ValueError("Неизвестный фильтр.")
            view.pop(value, None)
            await set_option(user_id, "stats_view", view)
            await show_stats(message, user_id)
        elif action == "filterpage":
            choice, separator, page = value.partition("|")
            if not separator or choice not in {"category", "card", "currency"}:
                raise ValueError("Неизвестная страница фильтра.")
            await filters_menu(
                message,
                user_id,
                choice,
                positive_int(page, "Неверный номер страницы."),
            )
        elif action == "periods":
            buttons = [
                (label, "period", key) for key, label in PERIODS.items()
            ] + [("Свои даты", "dates", ""), BACK]
            await send_text(
                message, "Выбери период:", keyboard(user_id, buttons)
            )
        elif action == "period":
            if value not in PERIODS:
                raise ValueError("Неизвестный период.")
            view["period"] = value
            await set_option(user_id, "stats_view", view)
            await show_stats(message, user_id)
        elif action in {"filters", "choose"}:
            await filters_menu(message, user_id, value)
        elif action in {"cat", "currency", "card"}:
            transactions, cards = await data_for(user_id)
            if action == "cat":
                categories = {
                    stable_key(tx.category): tx.category for tx in transactions
                }
                if value not in categories:
                    raise ValueError("Категория больше не найдена.")
                view["category"] = categories[value]
            elif action == "currency":
                if value not in {tx.currency for tx in transactions}:
                    raise ValueError("Валюта не найдена.")
                view["currency"] = value
            else:
                if value != "none" and positive_int(
                    value, "ID должен быть целым числом."
                ) not in {card.id for card in cards}:
                    raise ValueError("Карта не найдена.")
                view["card_id"] = (
                    None
                    if value == "none"
                    else positive_int(value, "ID должен быть целым числом.")
                )
            await set_option(user_id, "stats_view", view)
            await show_stats(message, user_id)
        elif action == "reset":
            await set_option(
                user_id,
                "stats_view",
                {
                    "period": view.get("period", "30"),
                    **(
                        {"start": view["start"], "end": view["end"]}
                        if view.get("period") == "custom"
                        else {}
                    ),
                },
            )
            await show_stats(message, user_id)
        elif action == "dates":
            await state.set_state(AnalysisInput.dates)
            await send_text(
                message,
                "Введи начало и конец: 2026-10-01 2026-10-31. Обе даты"
                " включены. Отмена: /analysis_cancel",
            )
        elif action == "search":
            await state.set_state(AnalysisInput.merchant)
            await send_text(
                message,
                "Введи название магазина или часть названия. Отмена:"
                " /analysis_cancel",
            )
        elif action == "export":
            await send_text(
                message,
                "Формат выгрузки выбранных покупок:",
                keyboard(user_id, [("CSV", "csv", ""), ("Excel", "xlsx", "")]),
            )
        elif action in {"important", "dismiss", "review"}:
            transactions, _ = await data_for(user_id, view, "subscriptions")
            subscriptions = detect_subscriptions(
                transactions, tz=user_timezone(view["timezone"])
            )
            if value not in {sub["key"] for sub in subscriptions}:
                raise ValueError(
                    "Подписка больше не найдена. Открой /subscriptions заново."
                )
            status = {
                "important": "important",
                "dismiss": "dismissed",
                "review": "review",
            }[action]
            await set_option(user_id, "subscription:" + value, status)
            await set_option(user_id, "last_advice", None)
            await send_text(
                message,
                {
                    "important": (
                        "Подписка отмечена важной. Предлагать её отмену больше"
                        " не буду."
                    ),
                    "dismiss": "Отмечено: это не подписка.",
                    "review": "Подписка снова включена в проверку.",
                }[action],
            )
        elif action == "ack":
            transactions, _ = await data_for(user_id)
            if positive_int(value, "ID должен быть целым числом.") not in {
                tx.id for tx in transactions
            }:
                raise ValueError("Операция не найдена.")
            await set_option(user_id, "ack:" + value, True)
            await send_text(
                message,
                "Покупка отмечена твоей. Этот сигнал больше не показывается.",
            )
        elif action == "cashback":
            await cashback_menu(message, user_id, state)
        elif action == "help":
            await send_text(message, HELP)
        else:
            await report(message, user_id, action, value)
    except (ValueError, KeyError) as error:
        await send_text(message, str(error))


@router_stats.message(Command("analysis_cancel"))
async def cancel_input(message: Message, state: FSMContext):
    await state.clear()
    await send_text(message, "Ввод отменён. Открой /stats, чтобы продолжить.")


@router_stats.message(
    AnalysisInput.dates,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_({BTN_STATS, BTN_ADVICE, BTN_CARDS, BTN_HISTORY, BTN_HELP}),
)
async def dates_input(message: Message, state: FSMContext):
    try:
        values = message.text.split()
        if len(values) != 2:
            raise ValueError("Нужны две даты в формате ГГГГ-ММ-ДД.")
        options = await get_options(message.from_user.id)
        view = dict(
            read_view(options),
            period="custom",
            start=values[0],
            end=values[1],
        )
        period_bounds(view)
        await set_option(message.from_user.id, "stats_view", view)
    except ValueError as error:
        await send_text(message, str(error))
        return
    await state.clear()
    await show_stats(message, message.from_user.id)


@router_stats.message(
    AnalysisInput.merchant,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_({BTN_STATS, BTN_ADVICE, BTN_CARDS, BTN_HISTORY, BTN_HELP}),
)
async def merchant_input(message: Message, state: FSMContext):
    query = message.text.strip()
    if not query or len(query) > 128:
        await send_text(message, "Нужно название от 1 до 128 символов.")
        return
    options = await get_options(message.from_user.id)
    view = dict(read_view(options), merchant=query)
    await set_option(message.from_user.id, "stats_view", view)
    await state.clear()
    await show_stats(message, message.from_user.id)


@router_stats.message(
    AnalysisInput.cashback,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_({BTN_STATS, BTN_ADVICE, BTN_CARDS, BTN_HISTORY, BTN_HELP}),
)
async def cashback_input(message: Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        cards = [c for c in await get_cards(user_id) if c.user_id == user_id]
        options = await get_options(user_id)
        rules = options.get("cashback_rules", [])
        parts = [part.strip() for part in message.text.split(";")]
        if parts[0].lower() == "удалить":
            if len(parts) != 4 or positive_int(
                parts[1], "ID карты должен быть целым числом."
            ) not in {card.id for card in cards}:
                raise ValueError(
                    "Формат: удалить; ID своей карты; валюта; категория"
                )
            key = (
                positive_int(parts[1], "ID карты должен быть целым числом."),
                parts[2].upper(),
                parts[3],
            )
            rule = None
        else:
            rule = parse_cashback_rule(message.text, cards)
            key = (rule["card_id"], rule["currency"], rule["category"])
        rules = [
            r
            for r in rules
            if (r["card_id"], r["currency"], r["category"]) != key
        ]
        if rule:
            rules.append(rule)
        await set_option(user_id, "cashback_rules", rules)
        await set_option(user_id, "last_advice", None)
    except (ValueError, KeyError) as error:
        await send_text(message, str(error))
        return
    await state.clear()
    await send_text(message, "Условия кэшбэка сохранены. Анализ: /advice")


@router_stats.message(
    Command(
        "compare",
        "daily",
        "weekly",
        "merchants",
        "forecast",
        "top",
        "details",
        "chart",
        "categories",
        "subscriptions",
        "security",
        "metrics",
    )
)
async def report_command(
    message: Message, command: CommandObject, state: FSMContext
):
    await state.clear()
    try:
        await report(
            message, message.from_user.id, command.command, command.args or ""
        )
    except ValueError as error:
        await send_text(message, str(error))


@router_stats.message(Command("export"))
async def export_command(
    message: Message, command: CommandObject, state: FSMContext
):
    await state.clear()
    kind = (command.args or "").strip().lower()
    if kind in {"csv", "xlsx"}:
        await report(message, message.from_user.id, kind)
    else:
        await send_text(
            message,
            "Выбери формат:",
            keyboard(
                message.from_user.id,
                [("CSV", "csv", ""), ("Excel", "xlsx", "")],
            ),
        )


@router_stats.message(Command("search"))
async def search_command(
    message: Message, command: CommandObject, state: FSMContext
):
    if command.args:
        query = command.args.strip()
        if len(query) > 128:
            await send_text(
                message, "Название магазина должно быть короче 129 символов."
            )
            return
        options = await get_options(message.from_user.id)
        view = dict(
            options.get("stats_view", {"period": "30"}), merchant=query
        )
        await set_option(message.from_user.id, "stats_view", view)
        await show_stats(message, message.from_user.id)
    else:
        await state.set_state(AnalysisInput.merchant)
        await send_text(message, "Введи магазин. Отмена: /analysis_cancel")


@router_stats.message(Command("cashback"))
async def cashback_command(message: Message, state: FSMContext):
    await cashback_menu(message, message.from_user.id, state)


@router_stats.message(Command("analysis", "analysis_help"))
async def help_command(message: Message, state: FSMContext):
    await state.clear()
    await send_text(message, HELP)


async def timezone_prompt(message, state):
    await state.set_state(AnalysisInput.timezone)
    await send_text(
        message,
        "Укажи смещение от UTC, например +03:00 для Москвы или +05:30. По"
        " умолчанию UTC+00:00. При сезонном переводе часов смещение нужно"
        " изменить. Отмена: /analysis_cancel",
    )


async def save_timezone(message, value, state):
    try:
        tz = user_timezone(value)
    except ValueError as error:
        await send_text(message, str(error))
        return
    minutes = int(tz.utcoffset(None).total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    offset = f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    await set_option(message.from_user.id, "timezone", offset)
    await set_option(message.from_user.id, "last_advice", None)
    await state.clear()
    await show_stats(message, message.from_user.id)


@router_stats.message(Command("timezone"))
async def timezone_command(
    message: Message, command: CommandObject, state: FSMContext
):
    await state.clear()
    if command.args:
        await save_timezone(message, command.args, state)
    else:
        await timezone_prompt(message, state)


@router_stats.message(
    AnalysisInput.timezone,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_({BTN_STATS, BTN_ADVICE, BTN_CARDS, BTN_HISTORY, BTN_HELP}),
)
async def timezone_input(message: Message, state: FSMContext):
    await save_timezone(message, message.text, state)
