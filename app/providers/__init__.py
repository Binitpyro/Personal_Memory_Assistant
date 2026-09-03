from app.providers.anthropic import AnthropicProvider
from app.providers.base import BaseProvider, ModelInfo, ValidationResult
from app.providers.gemini import GeminiProvider
from app.providers.manifest import (
    PROVIDER_IDS,
    env_base_url,
    get_configured_provider_ids,
    get_configured_provider_ids_async,
    get_default_chain,
    get_default_chain_async,
    is_loopback_url,
)
from app.providers.ollama import OllamaProvider
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.openrouter import OpenRouterProvider
from app.providers.registry import PROVIDER_REGISTRY, ProviderSpec, spec_of

__all__ = [
    "PROVIDER_IDS",
    "PROVIDER_REGISTRY",
    "AnthropicProvider",
    "BaseProvider",
    "GeminiProvider",
    "ModelInfo",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "OpenRouterProvider",
    "ProviderSpec",
    "ValidationResult",
    "create_provider",
    "env_base_url",
    "get_configured_provider_ids",
    "get_configured_provider_ids_async",
    "get_default_chain",
    "get_default_chain_async",
    "is_loopback_url",
    "spec_of",
]

# These providers are plain OpenAI-compatible endpoints: they differ from each
# other only by their registry spec and, for two of them, a default model. That
# used to be five modules (openai, openai_compatible, groq, lm_studio,
# nvidia_nim) whose entire body was `spec_of("<id>")` plus a string, dispatched
# by an if/elif chain keyed on the same id `spec_of` already keys on.
# openrouter is not in here because it genuinely overrides list_models.
_OPENAI_COMPATIBLE_DEFAULT_MODEL: dict[str, str | None] = {
    "openai": None,
    "openai_compatible": None,
    "lm_studio": None,
    "groq": "llama-3.3-70b-versatile",
    "nvidia_nim": "meta/llama-3.3-70b-instruct",
}

# LM Studio is a local server and create_provider has never forwarded a key to
# it. Kept explicit so the table above does not silently start sending one.
_NO_API_KEY = frozenset({"lm_studio"})


def create_provider(
    provider_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    timeout: float = 30.0,
) -> BaseProvider:
    if provider_id in _OPENAI_COMPATIBLE_DEFAULT_MODEL:
        return OpenAICompatibleProvider(
            spec_of(provider_id),
            api_key=None if provider_id in _NO_API_KEY else api_key,
            base_url=base_url,
            default_model=default_model or _OPENAI_COMPATIBLE_DEFAULT_MODEL[provider_id],
            timeout=timeout,
        )
    if provider_id == "gemini":
        return GeminiProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    if provider_id == "anthropic":
        return AnthropicProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    if provider_id == "openrouter":
        return OpenRouterProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    if provider_id == "ollama":
        return OllamaProvider(base_url=base_url, default_model=default_model, timeout=timeout)
    raise ValueError(f"Unknown provider ID: {provider_id}")
