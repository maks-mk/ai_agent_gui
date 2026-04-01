from enum import StrEnum


class ErrorType(StrEnum):
    ACCESS_DENIED = "ACCESS_DENIED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION = "VALIDATION"
    EXECUTION = "EXECUTION"
    TIMEOUT = "TIMEOUT"
    LOOP_DETECTED = "LOOP_DETECTED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    NETWORK = "NETWORK"
    CONFIG = "CONFIG"

def format_error(error_type: str, message: str) -> str:
    return f"ERROR[{error_type}]: {message}"
