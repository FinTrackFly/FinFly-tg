import asyncio
import logging

from gigachat.exceptions import RateLimitError
from langchain_core.runnables import Runnable
from langchain_gigachat import GigaChat

from finfly.core.config import (
    GIGACHAT_CREDENTIALS,
    GIGACHAT_MAX_CONCURRENCY,
    GIGACHAT_MODEL,
    GIGACHAT_SCOPE,
    GIGACHAT_TIMEOUT,
    GIGACHAT_VERIFY_SSL,
)

logger = logging.getLogger(__name__)

# Повторы при превышении лимита (429): паузы 2, 4, 8 секунд
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_DELAY = 2.0

# Общий клиент GigaChat для всех агентов
llm = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    scope=GIGACHAT_SCOPE,
    model=GIGACHAT_MODEL,
    verify_ssl_certs=GIGACHAT_VERIFY_SSL,
    profanity_check=False,
    temperature=0.1,
    timeout=GIGACHAT_TIMEOUT,
    # Повторы SDK отключены: он повторяет и таймауты, из-за чего
    # пользователь ждёт ответа минутами. Повторяем только 429 — в ask().
    max_retries=0,
)

# Ограничивает число одновременных запросов к GigaChat на весь бот
llm_semaphore = asyncio.Semaphore(GIGACHAT_MAX_CONCURRENCY)

# Платный тариф: независимых агентов можно запускать параллельно
PARALLEL = GIGACHAT_MAX_CONCURRENCY > 1


async def ask(runnable: Runnable, messages: list):
    """Запрос к GigaChat с учётом лимита потоков и повтором при 429."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            async with llm_semaphore:
                return await runnable.ainvoke(messages)
        except RateLimitError:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            delay = RATE_LIMIT_DELAY * 2**attempt
            logger.warning("GigaChat: лимит запросов, повтор через %gs", delay)
            await asyncio.sleep(delay)
    return None


async def upload_image(image: bytes) -> str:
    """Загружает изображение в GigaChat и возвращает id файла."""
    async with llm_semaphore:
        uploaded = await llm.aupload_file(("receipt.jpg", image))
    return uploaded.id_
