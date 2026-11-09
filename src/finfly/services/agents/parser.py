from langchain_core.messages import HumanMessage, SystemMessage

from finfly.core.constants import DEFAULT_CURRENCY
from finfly.services.agents.llm import ask, llm
from finfly.services.agents.prompts import PARSER_PROMPT
from finfly.services.agents.schemas import ParsedReceipt, ReceiptState

parser_llm = llm.with_structured_output(ParsedReceipt)


async def parser_agent(state: ReceiptState) -> ReceiptState:
    """Parser-агент: извлекает поля чека и отсеивает лишние операции."""
    if not state.get("raw_text"):
        return {"receipt": None}

    receipt = await ask(
        parser_llm,
        [
            SystemMessage(
                PARSER_PROMPT.format(default_currency=DEFAULT_CURRENCY)
            ),
            HumanMessage(state["raw_text"]),
        ],
    )
    currency = receipt.currency.strip().upper()[:3]
    receipt.currency = currency or DEFAULT_CURRENCY
    digits = "".join(filter(str.isdigit, receipt.card_last4))
    receipt.card_last4 = digits[-4:]
    return {"receipt": receipt}

# Module implemented by Dmitry Voronov
