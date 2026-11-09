from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from finfly.core.constants import CATEGORY_NAMES


class ReceiptItem(BaseModel):
    """Позиция чека."""

    name: str = Field(description="Название товара или услуги")
    quantity: float = Field(default=1, description="Количество")
    price: float = Field(description="Итоговая стоимость позиции")


class ParsedReceipt(BaseModel):
    """Структурированные данные чека или банковской операции."""

    is_receipt: bool = Field(
        description=(
            "true, если текст — чек, выписка или уведомление банка "
            "об операции; false для любого другого текста"
        ),
    )
    is_own_transfer: bool = Field(
        description=(
            "true, если это перевод между собственными счетами или "
            "картами пользователя, пополнение своего счёта/вклада, "
            "погашение своей кредитки — такие операции не являются "
            "расходом"
        ),
    )
    merchant: str = Field(
        description="Название магазина, заведения или получателя",
    )
    total: float = Field(description="Итоговая сумма к оплате (TOTAL)")
    currency: str = Field(
        description="Код валюты ISO 4217, например RUB, USD, EUR",
    )
    date: str = Field(
        default="",
        description="Дата покупки в формате YYYY-MM-DD, пусто если нет",
    )
    card_last4: str = Field(
        default="",
        description="Последние 4 цифры карты оплаты, пусто если нет",
    )
    items: list[ReceiptItem] = Field(
        default_factory=list,
        description="Позиции чека без налогов, скидок и итогов",
    )


class CategoryChoice(BaseModel):
    """Категория расхода."""

    category: Literal[CATEGORY_NAMES] = Field(
        description="Наиболее подходящая категория расхода",
    )


class ReceiptState(TypedDict, total=False):
    """Состояние графа обработки чека."""

    user_id: int
    image: bytes | None
    raw_text: str
    receipt: ParsedReceipt | None
    category: str
    description: str

# Module implemented by Dmitry Voronov
