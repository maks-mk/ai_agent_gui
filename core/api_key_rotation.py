from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from core.model_profiles import ModelProfileStore


class ApiKeyRotationError(RuntimeError):
    """Base error for model-profile API key rotation failures."""


class ApiKeyRotationExhaustedError(ApiKeyRotationError):
    """Raised when every key in the pool has exhausted rate-limit retries."""


class ApiKeyAuthenticationPoolError(ApiKeyRotationError):
    """Raised when every key in the pool is unauthorized or invalid."""


def _normalized_error_text(error: Exception) -> str:
    return " ".join(str(error).lower().split())


def classify_api_key_error(error: Exception) -> str | None:
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403}:
        return "auth"
    if status_code == 429:
        return "rate_limit"

    response = getattr(error, "response", None)
    response_status = getattr(response, "status_code", None)
    if response_status in {401, 403}:
        return "auth"
    if response_status == 429:
        return "rate_limit"

    text = _normalized_error_text(error)
    auth_markers = (
        "invalid_api_key",
        "incorrect api key",
        "authentication failed",
        "unauthorized",
        "forbidden",
        "permission denied",
        "error code: 401",
        "error code: 403",
    )
    rate_limit_markers = (
        "429",
        "too many requests",
        "rate limit",
        "quota exceeded",
        "insufficient_quota",
        "resource_exhausted",
    )
    if any(marker in text for marker in auth_markers):
        return "auth"
    if any(marker in text for marker in rate_limit_markers):
        return "rate_limit"
    return None


class RotatingChatModel:
    def __init__(
        self,
        *,
        config: Any,
        profile_id: str,
        profile_store_path: str | Path,
        llm_factory: Callable[..., Any],
        bound_tools: Sequence[Any] | None = None,
    ) -> None:
        self._config = config
        self._profile_id = str(profile_id or "").strip()
        self._profile_store = ModelProfileStore(profile_store_path)
        self._llm_factory = llm_factory
        self._bound_tools = list(bound_tools or [])
        self._prototype_model = self._build_model(self._initial_api_key())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._prototype_model, name)

    def bind_tools(self, tools: Sequence[Any]) -> "RotatingChatModel":
        return self.__class__(
            config=self._config,
            profile_id=self._profile_id,
            profile_store_path=self._profile_store.path,
            llm_factory=self._llm_factory,
            bound_tools=list(tools),
        )

    async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
        state = self._profile_store.get_api_key_state(self._profile_id)
        api_keys = list(state.get("api_keys") or [])
        max_attempts = max(1, len(api_keys) or 1)
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            state = self._profile_store.get_api_key_state(self._profile_id)
            active_key = str(state.get("current_key") or self._initial_api_key() or "").strip()
            model = self._build_model(active_key)
            try:
                return await model.ainvoke(input, **kwargs)
            except Exception as exc:
                error_kind = classify_api_key_error(exc)
                if error_kind is None or not api_keys:
                    raise
                last_error = exc
                state = self._profile_store.rotate_api_key(
                    self._profile_id,
                    active_key,
                    invalidate=error_kind == "auth",
                )
                next_key = str(state.get("current_key") or "").strip()
                if attempt >= max_attempts - 1 or not next_key or next_key == active_key:
                    raise self._terminal_error(error_kind, state, exc) from exc

        raise self._terminal_error("rate_limit", state, last_error or RuntimeError("API key rotation failed."))

    def _build_model(self, api_key: str) -> Any:
        model = self._llm_factory(self._config, api_key_override=api_key)
        if self._bound_tools:
            model = model.bind_tools(self._bound_tools)
        return model

    def _initial_api_key(self) -> str:
        if getattr(self._config, "provider", "") == "gemini":
            secret = getattr(self._config, "gemini_api_key", None)
        else:
            secret = getattr(self._config, "openai_api_key", None)
        return secret.get_secret_value() if secret is not None else ""

    def _model_label(self) -> str:
        if getattr(self._config, "provider", "") == "gemini":
            return str(getattr(self._config, "gemini_model", "") or self._profile_id or "model")
        return str(getattr(self._config, "openai_model", "") or self._profile_id or "model")

    def _terminal_error(self, error_kind: str, state: dict[str, Any], error: Exception) -> ApiKeyRotationError:
        model_label = self._model_label()
        if error_kind == "auth":
            return ApiKeyAuthenticationPoolError(
                f"All API keys for '{model_label}' are unauthorized or invalid. "
                "Open 'API Key Rotation' and remove or replace the broken keys."
            )
        return ApiKeyRotationExhaustedError(
            f"All API keys for '{model_label}' hit rate limits or exhausted quota. "
            "Try again later or update the rotation pool."
        )
