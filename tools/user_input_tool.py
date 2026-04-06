from typing import List

from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
def request_user_input(
    question: str,
    options: List[str],
    recommended: str = "",
) -> str:
    """
    Request an explicit user choice when the task cannot proceed without it.

    Use only when:
    - A decision has multiple valid paths with different outcomes.
    - External information is required and cannot be inferred from context.

    Do not use for uncertainty you can resolve with available tools.
    """
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
