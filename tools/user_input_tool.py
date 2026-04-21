from typing import List

from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
def request_user_input(
    question: str,
    options: List[str],
    recommended: str = "",
) -> str:
    """Ask one concrete user-choice question only when tools/context cannot resolve it. Use at most once per assistant turn; wait for the answer, then continue."""
    result = interrupt(
        {
            "kind": "user_choice",
            "question": question,
            "options": [str(option) for option in options],
            "recommended": str(recommended or ""),
        }
    )
    return str(result)


request_user_input.metadata = {"readOnlyHint": True}
