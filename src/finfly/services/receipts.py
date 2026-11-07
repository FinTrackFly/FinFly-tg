from time import perf_counter

from finfly.core.constants import DEFAULT_CATEGORY
from finfly.core.texts import NO_MERCHANT, TRANSFER_DESCRIPTION
from finfly.core.utils import parse_date
from finfly.db.models import Transaction
from finfly.db.requests import add_transaction, get_cards
from finfly.services.agents.graph import receipt_graph


async def find_card_id(user_id: int, last4: str) -> int | None:
    """Ищет карту пользователя по последним 4 цифрам."""
    if not last4:
        return None
    for card in await get_cards(user_id):
        if card.last4 == last4:
            return card.id
    return None


async def process_receipt(
    user_id: int,
    image: bytes | None = None,
    text: str = "",
) -> Transaction | None:
    """Прогоняет чек через агентов и сохраняет операцию.

    Возвращает None, если присланное не похоже на чек.
    """
    started = perf_counter()
    state = await receipt_graph.ainvoke(
        {"user_id": user_id, "image": image, "raw_text": text}
    )
    receipt = state.get("receipt")
    if receipt is None or not receipt.is_receipt:
        return None

    description = state.get("description", "")
    if receipt.is_own_transfer:
        description = TRANSFER_DESCRIPTION

    return await add_transaction(
        user_id=user_id,
        card_id=await find_card_id(user_id, receipt.card_last4),
        merchant=(receipt.merchant.strip() or NO_MERCHANT)[:128],
        amount=abs(receipt.total),
        currency=receipt.currency,
        purchase_date=parse_date(receipt.date),
        category=state.get("category", DEFAULT_CATEGORY),
        description=description,
        items=[item.model_dump() for item in receipt.items],
        raw_text=state.get("raw_text", ""),
        is_transfer=receipt.is_own_transfer,
        processing_time=perf_counter() - started,
    )
