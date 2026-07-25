from collections.abc import AsyncGenerator
from typing import Any, Protocol, TypedDict


class ModelInfo(TypedDict):
    id: str
    context_length: int | None
    pricing_hint: float | None  # Pricing hint (e.g. price per 1M tokens)
    family: str | None  # "reasoning" | "vision" | "tool-use" | "chat" etc.


class ValidationResult(TypedDict):
    ok: bool
    latency_ms: int
    models: list[ModelInfo]
    error: str | None
    error_code: (
        str | None
    )  # "auth_failed" | "wrong_base_url" | "rate_limited" | "provider_down" | "tls_error" | "network" | "empty"
    server_time: str | None


class BaseProvider(Protocol):
    spec: Any

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        default_model: str | None,
        timeout: float = 30.0,
    ) -> None: ...

    async def validate(self) -> ValidationResult: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str: ...

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]: ...

    async def close(self) -> None: ...
