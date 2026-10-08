import functools
import ssl
from collections.abc import AsyncGenerator
from typing import Any, Protocol, TypedDict

import httpx


@functools.cache
def _ssl_context() -> ssl.SSLContext:
    return httpx.create_ssl_context()


def new_async_client(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    """An AsyncClient that reuses one process-wide SSL context.

    httpx builds (and loads the CA bundle into) a fresh context per client, which
    measured 28-440 ms synchronously on the event loop for every provider built.
    """
    return httpx.AsyncClient(timeout=timeout, verify=_ssl_context())


class ProviderStreamError(httpx.RequestError):
    """An `error` object a provider sent inside an HTTP 200 stream, before any content.

    An httpx.RequestError so the llm_client fallback loop already catches it;
    `code` is what that loop forwards as the provider_error frame's code.
    """

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def stream_error_for(err: Any) -> ProviderStreamError:
    """Build the exception for an in-stream `error` value (a string, or {"message": ...})."""
    text = str(err.get("message") or err) if isinstance(err, dict) else str(err)
    overflow = "exceed_context_size_error" in text or "exceeds the available context size" in text
    return ProviderStreamError(text[:500], "context_overflow" if overflow else None)


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
        timeout: float | httpx.Timeout = 30.0,
    ) -> None: ...

    async def validate(self) -> ValidationResult: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]: ...

    async def close(self) -> None: ...
