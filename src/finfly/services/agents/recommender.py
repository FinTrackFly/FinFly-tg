from datetime import timedelta

from langchain_core.messages import HumanMessage, SystemMessage

from finfly.core.constants import ADVICE_PERIOD_DAYS
from finfly.core.utils import utc_now
from finfly.db.requests import get_cards, get_transactions_since
from finfly.services.agents.llm import ask, llm
from finfly.services.agents.prompts import RECOMMENDATIONS_PROMPT
from finfly.services.stats import build_summary, summary_for_prompt


async def recommendations_agent(user_id: int) -> str | None:
    """Recommendations-агент: советы по картам, кэшбэку и тратам.

    Возвращает None, если за период нет расходов.
    """
    since = utc_now() - timedelta(days=ADVICE_PERIOD_DAYS)
    summary = build_summary(await get_transactions_since(user_id, since))
    if not summary.count:
        return None

    cards = await get_cards(user_id)
    response = await ask(
        llm,
        [
            SystemMessage(
                RECOMMENDATIONS_PROMPT.format(days=ADVICE_PERIOD_DAYS)
            ),
            HumanMessage(summary_for_prompt(summary, cards)),
        ],
    )
    return response.text.strip()
