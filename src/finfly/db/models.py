from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Card(Base):
    """Карта (счёт) пользователя и условия кэшбэка по ней."""

    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(64))
    last4: Mapped[str | None] = mapped_column(String(4))
    cashback: Mapped[str] = mapped_column(Text, default="")


class Transaction(Base):
    """Операция (расход), полученная из чека или выписки."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL"),
    )
    merchant: Mapped[str] = mapped_column(String(128))
    amount: Mapped[float]
    currency: Mapped[str] = mapped_column(String(3))
    purchase_date: Mapped[date | None]
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list] = mapped_column(JSON, default=list)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    # Перевод между собственными счетами — не учитывается в расходах
    is_transfer: Mapped[bool] = mapped_column(default=False)
    # Пользователь исправлял категорию вручную (для метрик)
    category_edited: Mapped[bool] = mapped_column(default=False)
    processing_time: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CategoryMemory(Base):
    """Память о пользователе: какую категорию он выбрал для магазина."""

    __tablename__ = "category_memory"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    merchant_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    merchant: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
    )
