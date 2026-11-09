from langchain_core.messages import HumanMessage, SystemMessage

from finfly.services.agents.categorizer import describe_receipt
from finfly.services.agents.llm import ask, llm
from finfly.services.agents.prompts import DESCRIBER_PROMPT
from finfly.services.agents.schemas import ReceiptState


async def describer_agent(state: ReceiptState) -> ReceiptState:
    """Describer-агент: пишет понятное описание операции."""
    response = await ask(
        llm,
        [
            SystemMessage(DESCRIBER_PROMPT),
            HumanMessage(describe_receipt(state)),
        ],
    )
    description = response.text.strip().strip("«»\"'").split("\n")[0]
    return {"description": description}
