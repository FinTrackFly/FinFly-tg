from langchain_core.messages import HumanMessage

from finfly.services.agents.llm import ask, llm, upload_image
from finfly.services.agents.prompts import OCR_PROMPT
from finfly.services.agents.schemas import ReceiptState


async def ocr_agent(state: ReceiptState) -> ReceiptState:
    """OCR-агент: распознаёт текст на фотографии чека."""
    file_id = await upload_image(state["image"])
    message = HumanMessage(
        content=[
            {"type": "text", "text": OCR_PROMPT},
            {"type": "image", "file_id": file_id},
        ]
    )
    response = await ask(llm, [message])
    return {"raw_text": response.text.strip()}
