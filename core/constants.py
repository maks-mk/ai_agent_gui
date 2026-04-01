import sys
from pathlib import Path

# Agent version (single source of truth)
AGENT_VERSION = "0.62.5b"

# Определение корневой директории проекта
if getattr(sys, 'frozen', False):
    # Если запущено как exe, используем директорию исполняемого файла для конфигов,
    # но рабочей директорией оставим текущую (cwd), откуда запущен процесс.
    BASE_DIR = Path(sys.executable).parent
else:
    # core/constants.py -> core/ -> root/
    BASE_DIR = Path(__file__).resolve().parent.parent

# --- PROMPTS ---

SUMMARY_PROMPT_TEMPLATE = (
    "Current memory context:\n<previous_context>\n{summary}\n</previous_context>\n\n"
    "New events:\n{history_text}\n\n"
    "Update <previous_context>. This is a technical log of a software development session. "
    "Keep only key facts, decisions, and results. "
    "Remove chit-chat. Return only the updated context text."
)

REFLECTION_PROMPT = (
    "SYSTEM HINT: The previous tool execution failed. "
    "Review the error, correct your arguments, or use a different tool. "
    "Act immediately."
)

UNRESOLVED_TOOL_ERROR_PROMPT_TEMPLATE = (
    "UNRESOLVED TOOL FAILURE:\n"
    "{error_summary}\n\n"
    "Do not claim success, completion, or verified characteristics unless you have actually resolved this "
    "with later successful tool results. Either retry with corrected arguments, use another tool, "
    "or clearly explain the blocker to the user."
)

CRITIC_PROMPT_TEMPLATE = (
    "You are Critic, an internal verification node for an autonomous AI agent.\n"
    "Decide whether the user's task is practically completed based on the task, recent actions, and tool results.\n"
    "Focus on task completion, not on perfect factual certainty.\n"
    "Return FINISHED when the requested artifact, answer, or action appears completed and there are no explicit failures or clearly pending steps.\n"
    "Return INCOMPLETE only when something obvious is still missing, failed, or the agent clearly indicates more work remains.\n"
    "Never return FINISHED for an unresolved tool failure unless the assistant explicitly explains the blocker honestly "
    "or later successful tool results have resolved it.\n"
    "Do not require extra verification unless the user explicitly asked for verification, testing, or proof.\n"
    "Use CONTROL to decide the next graph action.\n"
    "Do not answer the user. Do not call tools. Return exactly four lines:\n"
    "STATUS: FINISHED or INCOMPLETE\n"
    "REASON: one short sentence\n"
    "NEXT_STEP: one short sentence or NONE\n\n"
    "CONTROL: FINISH_TURN or RETRY_AGENT\n\n"
    "Current task:\n"
    "{current_task}\n\n"
    "Conversation summary:\n"
    "{summary}\n\n"
    "Unresolved tool error summary:\n"
    "{unresolved_tool_error}\n\n"
    "Latest source before critic: {source}\n\n"
    "Recent trace:\n"
    "{trace}"
)

CRITIC_GENERIC_FEEDBACK = (
    "Task may still be incomplete. Review whether the requested result or artifact is already produced, "
    "and continue only if something obvious is still missing or failed."
)
