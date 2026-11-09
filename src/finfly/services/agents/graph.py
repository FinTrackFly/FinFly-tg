from langgraph.graph import END, START, StateGraph

from finfly.services.agents.categorizer import categorize_agent
from finfly.services.agents.describer import describer_agent
from finfly.services.agents.llm import PARALLEL
from finfly.services.agents.ocr import ocr_agent
from finfly.services.agents.parser import parser_agent
from finfly.services.agents.schemas import ReceiptState


def route_input(state: ReceiptState) -> str:
    """Фото идёт через OCR-агента, текст — сразу в Parser-агента."""
    return "ocr" if state.get("image") else "parser"


def route_parsed(state: ReceiptState) -> str | list[str]:
    """Не чек и переводы между своими счетами дальше не обрабатываются."""
    receipt = state.get("receipt")
    if receipt is None or not receipt.is_receipt or receipt.is_own_transfer:
        return END
    if PARALLEL:
        # Категория и описание не зависят друг от друга
        return ["categorize", "describe"]
    return "categorize"


def build_receipt_graph():
    """Собирает граф: OCR → Parser → Categorize + Describer.

    На бесплатном тарифе GigaChat (1 поток) Categorize и Describer
    выполняются по очереди, на платном — параллельно.
    """
    builder = StateGraph(ReceiptState)
    builder.add_node("ocr", ocr_agent)
    builder.add_node("parser", parser_agent)
    builder.add_node("categorize", categorize_agent)
    builder.add_node("describe", describer_agent)

    builder.add_conditional_edges(START, route_input, ["ocr", "parser"])
    builder.add_edge("ocr", "parser")
    builder.add_conditional_edges(
        "parser",
        route_parsed,
        ["categorize", "describe", END],
    )
    if PARALLEL:
        builder.add_edge("categorize", END)
    else:
        builder.add_edge("categorize", "describe")
    builder.add_edge("describe", END)
    return builder.compile()


receipt_graph = build_receipt_graph()
