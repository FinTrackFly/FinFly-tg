from langchain_core.messages import HumanMessage, SystemMessage

from finfly.core.constants import (
    CATEGORY_NAMES,
    DEFAULT_CATEGORY,
    MEMORY_EXAMPLES_LIMIT,
)
from finfly.db.requests import get_category_memory, get_remembered_category
from finfly.services.agents.llm import ask, llm
from finfly.services.agents.prompts import (
    CATEGORIZE_MEMORY_PROMPT,
    CATEGORIZE_PROMPT,
)
from finfly.services.agents.schemas import CategoryChoice, ReceiptState

categorize_llm = llm.with_structured_output(CategoryChoice)


def describe_receipt(state: ReceiptState) -> str:
    """Короткое текстовое представление чека для промптов агентов."""
    receipt = state["receipt"]
    items = ", ".join(item.name for item in receipt.items[:15])
    return f"Магазин: {receipt.merchant}\nПозиции: {items or 'нет'}"


async def categorize_agent(state: ReceiptState) -> ReceiptState:
    """Categorize-агент: определяет категорию с учётом памяти."""
    user_id = state["user_id"]
    merchant = state["receipt"].merchant

    # Пользователь уже исправлял категорию для этого магазина
    remembered = await get_remembered_category(user_id, merchant)
    if remembered:
        return {"category": remembered}

    system = CATEGORIZE_PROMPT.format(categories=", ".join(CATEGORY_NAMES))
    memory = await get_category_memory(user_id, MEMORY_EXAMPLES_LIMIT)
    if memory:
        examples = "\n".join(f"{m.merchant} → {m.category}" for m in memory)
        system += CATEGORIZE_MEMORY_PROMPT.format(examples=examples)

    choice = await ask(
        categorize_llm,
        [SystemMessage(system), HumanMessage(describe_receipt(state))],
    )
    category = choice.category
    if category not in CATEGORY_NAMES:
        category = DEFAULT_CATEGORY
    return {"category": category}
