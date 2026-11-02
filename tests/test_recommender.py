import asyncio
import json
import sys
from datetime import datetime, date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from finfly.services.agents import recommender as r
from finfly.services import stats as s
from test_analysis import tx, subscription_rows


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(r, "allow_action", lambda *args, **kwargs: True)

    async def choose(client, messages):
        data = json.loads(messages[-1].content)
        return SimpleNamespace(
            text=json.dumps({"ids": [data["candidates"][0]["id"]]})
        )

    ask = AsyncMock(side_effect=choose)
    monkeypatch.setitem(
        sys.modules,
        "finfly.services.agents.llm",
        SimpleNamespace(ask=ask, llm=object()),
    )
    return ask


@pytest.fixture
def inputs(monkeypatch):
    transactions = [
        tx(1, day=date(2026, 10, 15)),
        tx(2, day=date(2026, 10, 20)),
        tx(99, user=2, amount=99999),
    ]
    monkeypatch.setattr(
        r, "load_transactions", AsyncMock(return_value=transactions)
    )
    monkeypatch.setattr(r, "get_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(r, "utc_now", lambda: datetime(2026, 11, 1, 12))
    return transactions


@pytest.mark.parametrize(
    "output",
    [
        "{}",
        '{"ids":["unknown"]}',
        '{"ids":["a0","a0"]}',
        '{"ids":[100]}',
        '"hello"',
        "not json",
    ],
)
def test_invalid_model_answers_rejected(output):
    with pytest.raises((ValueError, TypeError)):
        r.validated_selection(output, [{"id": "a0"}])


def test_model_cannot_insert_its_numbers_or_instructions():
    candidates = [{"id": "a0", "detail": "100 ₽"}]
    assert (
        r.validated_selection(
            '{"ids":["a0"], "detail":"Cancel important service and spend'
            ' 9999"}',
            candidates,
        )
        == candidates
    )


@pytest.mark.asyncio
async def test_cache_scoped_to_user_and_changes_invalidate(
    database, inputs, llm
):
    result = await r.generate_advice(1)
    assert "99 999" not in result.render(True)
    second = await r.generate_advice(1)
    assert second.cached and llm.await_count == 1
    inputs[0].amount = 200
    changed = await r.generate_advice(1)
    assert not changed.cached and llm.await_count == 2
    assert (await s.get_options(2)).get("last_advice") is None


@pytest.mark.asyncio
async def test_important_subscription_never_cancellation_candidate(
    database, monkeypatch, llm
):
    rows = subscription_rows(
        [date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)]
    )
    monkeypatch.setattr(r, "load_transactions", AsyncMock(return_value=rows))
    monkeypatch.setattr(r, "get_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(r, "utc_now", lambda: datetime(2026, 11, 1, 12))
    await s.set_option(1, "goal", "subscriptions")
    normal = await r.generate_advice(1)
    assert any(
        a["kind"] == "subscriptions" and "Video Service" in a["title"]
        for a in normal.actions
    )
    key = s.detect_subscriptions(rows, date(2026, 11, 1))[0]["key"]
    await s.set_option(1, "subscription:" + key, "important")
    protected = await r.generate_advice(1)
    assert "Проверь подписку" not in protected.render(True)
    # Пересоздание настроек не требуется: отметка хранится в БД.
    assert (await s.get_options(1))["subscription:" + key] == "important"


@pytest.mark.asyncio
async def test_changed_preferences_discard_stale_advice(database, inputs, llm):
    async def change(client, messages):
        await s.set_option(1, "protected_categories", ["Продукты"])
        return SimpleNamespace(text='{"ids":["a0"]}')

    llm.side_effect = change
    result = await r.generate_advice(1)
    assert result.actions == []
    assert (await s.get_options(1)).get("last_advice") is None


@pytest.mark.asyncio
async def test_empty_history_does_not_call_model(database, inputs, llm):
    inputs.clear()
    assert await r.generate_advice(1) is None
    llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_and_bad_response_have_factual_fallback(
    database, inputs, llm, monkeypatch
):
    async def timeout(*args):
        await asyncio.sleep(1)

    llm.side_effect = timeout
    monkeypatch.setattr(r, "ADVICE_TIMEOUT", 0.001)
    result = await r.generate_advice(1)
    assert result.actions and "расчётные подсказки" in result.note
    llm.side_effect = None
    llm.return_value = SimpleNamespace(text="Просто потратьте 99999 рублей")
    result = await r.generate_advice(1, force=True)
    assert "99999" not in result.render(True)


@pytest.mark.asyncio
async def test_protected_category_and_user_notes(database, inputs, llm):
    await s.set_option(1, "protected_categories", ["Продукты"])
    await s.set_option(1, "preferences", "Не экономить на еде")
    result = await r.generate_advice(1)
    assert not any("Продукты" in a["title"] for a in result.actions)
    sent = json.loads(llm.await_args.args[1][-1].content)
    assert sent["preferences"] == "Не экономить на еде"


@pytest.mark.parametrize(
    "saved",
    [
        None,
        [],
        {},
        {"schema_version": 1},
        {"schema_version": 99},
        {
            "schema_version": 1,
            "title": "old",
            "actions": [],
            "fingerprint": 123,
            "goal": "general",
        },
    ],
)
def test_incompatible_cache_ignored(saved):
    assert r.AdviceResult.from_saved(saved) is None


def test_validator_rejects_more_than_three_actions():
    with pytest.raises(ValueError):
        r.validated_selection(
            '{"ids":["a0","a1","a2","a3"]}',
            [{"id": f"a{i}"} for i in range(4)],
        )


@pytest.mark.asyncio
async def test_savings_fallback_respects_goal(database, inputs, llm):
    await s.set_option(1, "goal", "save")
    llm.side_effect = RuntimeError("offline")
    result = await r.generate_advice(1)
    assert result.actions[0]["kind"] == "save"
    assert all(
        action["kind"] in {"save", "subscriptions", "cashback", "general"}
        for action in result.actions
    )


def test_candidates_deduplicate_category_currency():
    summary = s.build_summary([tx()])
    candidates = r.make_candidates(
        summary, s.build_summary([]), [tx()], [], {}, date(2026, 11, 1)
    )
    selected = r.rank_candidates(candidates, "general")
    assert (
        len([item for item in selected if item.get("category") == "Продукты"])
        == 1
    )


@pytest.mark.asyncio
async def test_cancellation_propagates(database, inputs, llm):
    llm.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await r.generate_advice(1)
    assert (await s.get_options(1)).get("last_advice") is None


@pytest.mark.asyncio
async def test_irrelevant_old_transaction_does_not_break_cache(
    database, inputs, llm
):
    original = await r.generate_advice(1)
    inputs.append(tx(123, day=date(2025, 1, 2), category="Другое"))
    repeated = await r.generate_advice(1)
    assert repeated.cached and repeated.fingerprint == original.fingerprint
    assert llm.await_count == 1


@pytest.mark.asyncio
async def test_force_and_question_are_rate_limited(
    database, inputs, llm, monkeypatch
):
    monkeypatch.setattr(r, "allow_action", s.allow_action)
    await r.generate_advice(1)
    await r.generate_advice(1, force=True)
    result = await r.generate_advice(1, question="Что изменилось?")
    assert llm.await_count == 1
    assert "15 секунд" in result.note


@pytest.mark.asyncio
async def test_corrupt_saved_advice_regenerates(database, inputs, llm):
    await s.set_option(1, "last_advice", {"schema_version": 1, "actions": 55})
    result = await r.generate_advice(1)
    assert result.actions and not result.cached


@pytest.mark.asyncio
async def test_refunds_only_do_not_divide_by_zero(database, inputs, llm):
    inputs[:] = [tx(amount=-100, day=date(2026, 10, 15))]
    result = await r.generate_advice(1)
    assert result is not None and result.actions
