from datetime import datetime

from sqlalchemy import delete, select, update

from finfly.core.utils import normalize_merchant
from finfly.db.engine import session_maker
from finfly.db.models import Card, CategoryMemory, Transaction

###
### Операции
###


async def add_transaction(**fields) -> Transaction:
    """Сохраняет новую операцию."""
    async with session_maker() as session:
        transaction = Transaction(**fields)
        session.add(transaction)
        await session.commit()
        return transaction


async def get_transaction(user_id: int, tx_id: int) -> Transaction | None:
    """Возвращает операцию пользователя по id."""
    async with session_maker() as session:
        return await session.scalar(
            select(Transaction).where(
                Transaction.id == tx_id,
                Transaction.user_id == user_id,
            )
        )


async def get_last_transactions(
    user_id: int,
    limit: int,
) -> list[Transaction]:
    """Возвращает последние операции пользователя (новые сверху)."""
    async with session_maker() as session:
        result = await session.scalars(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.id.desc())
            .limit(limit)
        )
        return list(result)


async def get_transactions_since(
    user_id: int,
    since: datetime | None = None,
) -> list[Transaction]:
    """Возвращает операции, добавленные начиная с since (None — все)."""
    query = select(Transaction).where(Transaction.user_id == user_id)
    if since is not None:
        query = query.where(Transaction.created_at >= since)
    async with session_maker() as session:
        return list(await session.scalars(query))


async def update_transaction(user_id: int, tx_id: int, **fields) -> None:
    """Обновляет поля операции пользователя."""
    async with session_maker() as session:
        await session.execute(
            update(Transaction)
            .where(Transaction.id == tx_id, Transaction.user_id == user_id)
            .values(**fields)
        )
        await session.commit()


async def set_transaction_category(
    user_id: int,
    tx_id: int,
    category: str,
) -> None:
    """Меняет категорию операции и запоминает выбор пользователя."""
    transaction = await get_transaction(user_id, tx_id)
    if transaction is None:
        return
    await update_transaction(
        user_id,
        tx_id,
        category=category,
        category_edited=True,
    )
    await remember_category(user_id, transaction.merchant, category)


async def delete_transaction(user_id: int, tx_id: int) -> None:
    """Удаляет операцию пользователя."""
    async with session_maker() as session:
        await session.execute(
            delete(Transaction).where(
                Transaction.id == tx_id,
                Transaction.user_id == user_id,
            )
        )
        await session.commit()


###
### Карты
###


async def get_cards(user_id: int) -> list[Card]:
    """Возвращает карты пользователя."""
    async with session_maker() as session:
        result = await session.scalars(
            select(Card).where(Card.user_id == user_id).order_by(Card.id)
        )
        return list(result)


async def add_card(
    user_id: int,
    name: str,
    last4: str | None,
    cashback: str,
) -> Card:
    """Добавляет карту пользователю."""
    async with session_maker() as session:
        card = Card(
            user_id=user_id,
            name=name,
            last4=last4,
            cashback=cashback,
        )
        session.add(card)
        await session.commit()
        return card


async def delete_card(user_id: int, card_id: int) -> None:
    """Удаляет карту, у её операций счёт становится «не указан»."""
    async with session_maker() as session:
        await session.execute(
            update(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.card_id == card_id,
            )
            .values(card_id=None)
        )
        await session.execute(
            delete(Card).where(Card.id == card_id, Card.user_id == user_id)
        )
        await session.commit()


###
### Память о пользователе
###


async def remember_category(
    user_id: int,
    merchant: str,
    category: str,
) -> None:
    """Запоминает, к какой категории пользователь относит магазин."""
    key = normalize_merchant(merchant)
    if not key:
        return
    async with session_maker() as session:
        memory = await session.get(CategoryMemory, (user_id, key))
        if memory is None:
            session.add(
                CategoryMemory(
                    user_id=user_id,
                    merchant_key=key,
                    merchant=merchant,
                    category=category,
                )
            )
        else:
            memory.merchant = merchant
            memory.category = category
        await session.commit()


async def get_remembered_category(user_id: int, merchant: str) -> str | None:
    """Возвращает категорию, которую пользователь выбирал для магазина."""
    async with session_maker() as session:
        memory = await session.get(
            CategoryMemory,
            (user_id, normalize_merchant(merchant)),
        )
        return memory.category if memory else None


async def get_category_memory(
    user_id: int,
    limit: int,
) -> list[CategoryMemory]:
    """Возвращает последние правки категорий пользователя."""
    async with session_maker() as session:
        result = await session.scalars(
            select(CategoryMemory)
            .where(CategoryMemory.user_id == user_id)
            .order_by(CategoryMemory.updated_at.desc())
            .limit(limit)
        )
        return list(result)

# Module implemented by Dmitry Voronov
