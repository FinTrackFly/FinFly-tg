import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from finfly.routers.user import stats as sr
from finfly.routers.user import advice as ar
from finfly.services import stats as s
from test_analysis import tx


@pytest.fixture(autouse=True)
def no_navigation_throttle(monkeypatch):
    monkeypatch.setattr(sr, "allow_action", lambda *args, **kwargs: True)


def message(user=1):
    value = AsyncMock(spec=Message)
    value.from_user = SimpleNamespace(id=user)
    value.chat = SimpleNamespace(id=user)
    value.bot = object()
    value.answer = AsyncMock(return_value=AsyncMock())
    value.answer_document = AsyncMock()
    return value


@pytest.mark.asyncio
async def test_callbacks_cannot_access_another_user(database):
    msg = message()
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=2), message=msg, answer=AsyncMock()
    )
    await sr.analysis_action(
        cb, sr.AnalysisAction(owner=1, action="csv"), AsyncMock()
    )
    msg.answer_document.assert_not_awaited()
    assert "другому" in cb.answer.await_args.args[0]
    await ar.advice_action(
        cb, ar.AdviceAction(owner=1, action="why", value="x"), AsyncMock()
    )
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_requests_single_user_and_other_users(monkeypatch):
    entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def slow(msg, uid, token, **kwargs):
        calls.append(uid)
        if uid == 2:
            second_entered.set()
        entered.set()
        await release.wait()

    monkeypatch.setattr(ar, "produce_advice", slow)
    msg = message()
    first = asyncio.create_task(ar.run_advice(msg, 1))
    await entered.wait()
    await ar.run_advice(msg, 1)
    second = asyncio.create_task(ar.run_advice(message(2), 2))
    await asyncio.wait_for(second_entered.wait(), timeout=2)
    assert calls == [1, 2]
    release.set()
    await asyncio.gather(first, second)
    assert not ar.pending_advice
    assert "уже готовится" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cancel_and_stale_cancel_button(monkeypatch):
    entered = asyncio.Event()

    async def slow(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ar, "produce_advice", slow)
    msg = message()
    first = asyncio.create_task(ar.run_advice(msg, 1))
    await entered.wait()
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=1), message=msg, answer=AsyncMock()
    )
    await ar.advice_action(
        cb, ar.AdviceAction(owner=1, action="cancel", value="old"), AsyncMock()
    )
    assert not ar.pending_advice[1][0].done()
    await ar.cancel_advice(msg)
    await first
    assert not ar.pending_advice


@pytest.mark.asyncio
async def test_details_paginate_and_top_currencies(database, monkeypatch):
    rows = [tx(i, day=date(2026, 10, 20), amount=i) for i in range(1, 12)] + [
        tx(99, currency="USD", day=date(2026, 10, 20), transfer=True)
    ]
    monkeypatch.setattr(sr, "load_transactions", AsyncMock(return_value=rows))
    monkeypatch.setattr(sr, "get_cards", AsyncMock(return_value=[]))
    await s.set_option(1, "stats_view", {"period": "all"})
    msg = message()
    await sr.report(msg, 1, "details", "0")
    text = msg.answer.await_args.args[0]
    assert (
        "страница 1/2" in text and "11.00 ₽" in text and "3.00 ₽" not in text
    )
    top = sr.build_top_text(rows)
    assert "USD" not in top and "11.00 ₽" in top and "6.00 ₽" not in top


@pytest.mark.asyncio
async def test_export_uses_selected_filters_and_owner(database, monkeypatch):
    rows = [tx(1), tx(2, user=2), tx(3, currency="USD")]
    monkeypatch.setattr(sr, "load_transactions", AsyncMock(return_value=rows))
    monkeypatch.setattr(sr, "get_cards", AsyncMock(return_value=[]))
    await s.set_option(1, "stats_view", {"period": "all", "currency": "RUB"})
    msg = message()
    await sr.report(msg, 1, "csv")
    file = msg.answer_document.await_args.args[0]
    content = file.data.decode("utf-8-sig")
    assert len(content.splitlines()) == 2
    assert "USD" not in content


@pytest.mark.asyncio
async def test_settings_persist_category_and_reset(database, monkeypatch):
    monkeypatch.setattr(
        sr, "load_transactions", AsyncMock(return_value=[tx()])
    )
    monkeypatch.setattr(sr, "get_cards", AsyncMock(return_value=[]))
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=1), message=message(), answer=AsyncMock()
    )
    await sr.analysis_action(
        cb,
        sr.AnalysisAction(
            owner=1, action="cat", value=s.stable_key("Продукты")
        ),
        AsyncMock(),
    )
    assert (await s.get_options(1))["stats_view"]["category"] == "Продукты"
    await sr.analysis_action(
        cb, sr.AnalysisAction(owner=1, action="reset"), AsyncMock()
    )
    assert "category" not in (await s.get_options(1))["stats_view"]


def test_all_menu_buttons_fit_telegram_callback_limit():
    for menu in (
        sr.menu(999999999999),
        ar.result_menu(999999999999, SimpleNamespace(fingerprint="a" * 64)),
    ):
        for row in menu.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64


@pytest.mark.asyncio
async def test_real_dispatcher_routes_analysis_before_receipt_parser(
    database, monkeypatch
):
    from datetime import datetime
    from aiogram import Bot, Dispatcher
    from aiogram.client.session.base import BaseSession
    from aiogram.methods import (
        SendMessage,
        SendChatAction,
        SendDocument,
        EditMessageText,
        AnswerCallbackQuery,
    )
    from aiogram.types import Update, User, Chat, CallbackQuery
    from finfly.db.models import Transaction
    from finfly.routers.user.main import register_user_handlers

    class Session(BaseSession):
        def __init__(self):
            super().__init__()
            self.sent = []

        async def make_request(self, bot, method, timeout=None):
            self.sent.append(method)
            if isinstance(method, (SendChatAction, AnswerCallbackQuery)):
                return True
            if isinstance(method, EditMessageText):
                return True
            if isinstance(method, SendDocument):
                return Message(
                    message_id=len(self.sent),
                    date=datetime(2026, 11, 1),
                    chat=Chat(id=int(method.chat_id), type="private"),
                    caption=method.caption,
                ).as_(bot)
            if isinstance(method, SendMessage):
                return Message(
                    message_id=len(self.sent),
                    date=datetime(2026, 11, 1),
                    chat=Chat(id=int(method.chat_id), type="private"),
                    text=method.text,
                ).as_(bot)
            raise AssertionError(
                f"Unexpected Telegram call: {type(method).__name__}"
            )

        async def close(self):
            pass

        async def stream_content(self, *args, **kwargs):
            yield b""

    async with database() as db:
        db.add_all(
            [
                Transaction(
                    id=1,
                    user_id=1,
                    merchant="Свой магазин",
                    amount=100,
                    currency="RUB",
                    category="Продукты",
                    card_id=None,
                    purchase_date=date(2026, 10, 20),
                ),
                Transaction(
                    id=2,
                    user_id=2,
                    merchant="Чужой магазин",
                    amount=999,
                    currency="RUB",
                    category="Продукты",
                    card_id=None,
                    purchase_date=date(2026, 10, 20),
                ),
            ]
        )
        await db.commit()
    session = Session()
    bot = Bot("123456:TEST_ONLY_NO_NETWORK", session=session)
    dispatcher = Dispatcher()
    register_user_handlers(dispatcher)
    for index, command in enumerate(
        (
            "/stats",
            "/compare",
            "/subscriptions",
            "/security",
            "/analysis",
            "/daily",
            "/weekly",
            "/top",
            "/details",
            "/chart",
            "/categories",
            "/export csv",
            "/export xlsx",
            "/forecast",
            "/metrics",
            "/preferences",
        ),
        1,
    ):
        event = Message(
            message_id=index,
            date=datetime(2026, 11, 1),
            chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="Test"),
            text=command,
        )
        await dispatcher.feed_update(
            bot, Update(update_id=index, message=event)
        )
    texts = "\n".join(
        method.text
        for method in session.sent
        if isinstance(method, SendMessage)
    )
    assert "100.00 ₽" in texts
    assert "Чужой магазин" not in texts and "999.00" not in texts
    assert "Вероятные подписки" in texts and "Проверка операций" in texts
    assert "/analysis_cancel" in texts
    context = dispatcher.fsm.get_context(bot=bot, chat_id=1, user_id=1)
    update_id = 100

    async def tap(action, value="", owner=1, actor=1, data=None):
        nonlocal update_id
        update_id += 1
        callback = CallbackQuery(
            id=str(update_id),
            from_user=User(id=actor, is_bot=False, first_name="Test"),
            chat_instance="test",
            data=data
            or sr.AnalysisAction(
                owner=owner, action=action, value=value
            ).pack(),
            message=Message(
                message_id=99,
                date=datetime(2026, 11, 1),
                chat=Chat(id=1, type="private"),
            ),
        )
        await dispatcher.feed_update(
            bot, Update(update_id=update_id, callback_query=callback)
        )

    async def submit(text):
        nonlocal update_id
        update_id += 1
        event = Message(
            message_id=update_id,
            date=datetime(2026, 11, 1),
            chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="Test"),
            text=text,
        )
        await dispatcher.feed_update(
            bot, Update(update_id=update_id, message=event)
        )

    await tap("dates")
    assert await context.get_state() == sr.AnalysisInput.dates.state
    await tap("choose", "category")
    assert await context.get_state() is None
    await tap("dates")
    await tap("unused", data=sr.StatsPeriod(days=7).pack())
    assert await context.get_state() is None
    await tap("search")
    await submit("Свой")
    assert (await s.get_options(1))["stats_view"]["merchant"] == "Свой"
    await tap("unfilter", "merchant")
    assert "merchant" not in (await s.get_options(1))["stats_view"]
    await tap("details", "bad")
    assert "Страница должна быть" in session.sent[-1].text
    await tap("card", "bad")
    assert "ID должен быть" in session.sent[-1].text
    await tap("timezone")
    await submit("+03:00")
    assert (await s.get_options(1))["timezone"] == "+03:00"
    await tap("dates")
    await submit("2026-10-01 2026-10-31")
    assert (await s.get_options(1))["stats_view"]["period"] == "custom"
    assert await context.get_state() is None
    previous = await s.get_options(1)
    await tap("period", "all", actor=2)
    assert "другому пользователю" in session.sent[-1].text
    assert await s.get_options(1) == previous

    from dataclasses import asdict

    result = ar.AdviceResult(
        title="Совет",
        actions=[
            {
                "id": "a0",
                "kind": "save",
                "title": "Проверь расходы",
                "detail": "Продукты: 100 рублей",
                "evidence": "Одна покупка",
            }
        ],
        fingerprint="a" * 64,
        goal="save",
    )
    monkeypatch.setattr(ar, "generate_advice", AsyncMock(return_value=result))
    await tap("dates")
    await submit(ar.BTN_ADVICE)
    assert await context.get_state() is None
    assert any(
        isinstance(method, EditMessageText)
        and "Проверь расходы" in method.text
        for method in session.sent
    )
    await s.set_option(1, "last_advice", asdict(result))
    for action in ("long", "why", "up"):
        await tap(
            "unused",
            data=ar.AdviceAction(
                owner=1, action=action, value="a" * 16
            ).pack(),
        )
    assert (await s.get_options(1))["feedback:last"]["rating"] == "up"
    await tap("unused", data=ar.AdviceAction(owner=1, action="ask").pack())
    assert await context.get_state() == ar.AdviceInput.question.state
    await submit("Что изменилось?")
    assert await context.get_state() is None
    assert (
        ar.generate_advice.await_args.kwargs["question"] == "Что изменилось?"
    )
    await tap("dates")
    await submit(ar.BTN_CARDS)
    assert await context.get_state() is None
    await dispatcher.storage.close()
    await bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["", ";", "удалить", "удалить;abc;RUB;*", "abc;RUB;*;5;нет"]
)
async def test_malformed_cashback_input_is_friendly(
    database, monkeypatch, value
):
    monkeypatch.setattr(
        sr,
        "get_cards",
        AsyncMock(return_value=[SimpleNamespace(id=1, user_id=1)]),
    )
    msg = message()
    msg.text = value
    state = AsyncMock()
    await sr.cashback_input(msg, state)
    assert (
        "Формат" in msg.answer.await_args.args[0]
        or "ID карты" in msg.answer.await_args.args[0]
    )
    state.clear.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,value",
    [
        ("details", "foo"),
        ("card", "abc"),
        ("ack", "abc"),
        ("filterpage", "category|abc"),
        ("filterpage", "bad"),
        ("period", "unknown"),
    ],
)
async def test_malformed_callbacks_are_friendly(
    database, monkeypatch, action, value
):
    monkeypatch.setattr(
        sr, "load_transactions", AsyncMock(return_value=[tx()])
    )
    monkeypatch.setattr(sr, "get_cards", AsyncMock(return_value=[]))
    msg = message()
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=1), message=msg, answer=AsyncMock()
    )
    await sr.analysis_action(
        cb, sr.AnalysisAction(owner=1, action=action, value=value), AsyncMock()
    )
    text = msg.answer.await_args.args[0]
    assert "invalid literal" not in text
    assert any(
        word in text for word in ("Страница", "ID", "номер", "Неизвестн")
    )


@pytest.mark.asyncio
async def test_corrupt_ack_does_not_break_security():
    msg = message()
    await sr.show_security(msg, 1, [], {"ack:abc": True})
    assert msg.answer.await_count >= 2


@pytest.mark.asyncio
async def test_busy_question_keeps_input_state(monkeypatch):
    waiting = asyncio.create_task(asyncio.Event().wait())
    ar.pending_advice[1] = (waiting, "token")
    try:
        msg = message()
        msg.text = "Вопрос"
        state = AsyncMock()
        await ar.question_input(msg, state)
        state.clear.assert_not_awaited()
        assert "повтори" in msg.answer.await_args.args[0]
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        ar.pending_advice.clear()


@pytest.mark.asyncio
async def test_feedback_replaces_old_records(database):
    await s.set_option(1, "feedback:old", "up")
    await s.set_option(2, "feedback:other", "up")
    await s.set_option(
        1, "feedback:last", {"rating": "down", "fingerprint": "a" * 64}
    )
    assert set(await s.get_options(1)) == {"feedback:last"}
    assert "feedback:other" in await s.get_options(2)


@pytest.mark.asyncio
async def test_filter_pagination_and_export_limit(database, monkeypatch):
    rows = [tx(i, category=f"Категория {i}") for i in range(40)]
    monkeypatch.setattr(sr, "load_transactions", AsyncMock(return_value=rows))
    monkeypatch.setattr(sr, "get_cards", AsyncMock(return_value=[]))
    msg = message()
    await sr.filters_menu(msg, 1, "category")
    assert msg.answer.await_count == 1
    markup = msg.answer.await_args.kwargs["reply_markup"]
    assert (
        len([button for row in markup.inline_keyboard for button in row]) == 14
    )
    assert "страница 1/4" in msg.answer.await_args.args[0]
    monkeypatch.setattr(sr, "EXPORT_LIMIT", 10)
    await s.set_option(1, "stats_view", {"period": "all"})
    await sr.report(msg, 1, "xlsx")
    assert (
        "выгружено 10 из 40"
        in msg.answer_document.await_args.kwargs["caption"]
    )


@pytest.mark.asyncio
async def test_advice_old_cache_button_is_safe(database):
    await s.set_option(1, "last_advice", {"fingerprint": "a" * 64})
    msg = message()
    cb = SimpleNamespace(
        from_user=SimpleNamespace(id=1), message=msg, answer=AsyncMock()
    )
    await ar.advice_action(
        cb, ar.AdviceAction(owner=1, action="why", value="a" * 16), AsyncMock()
    )
    assert "Открой /advice" in msg.answer.await_args.args[0]
