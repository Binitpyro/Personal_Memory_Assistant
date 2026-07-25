from app.providers.anthropic import AnthropicProvider
from app.providers.base import BaseProvider, ModelInfo, ValidationResult
from app.providers.gemini import GeminiProvider
from app.providers.groq import GroqProvider
from app.providers.lm_studio import LMStudioProvider
from app.providers.manifest import PROVIDER_IDS, get_configured_provider_ids
from app.providers.nvidia_nim import NvidiaNimProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider
from app.providers.openai_compatible import OpenAICompatibleProviderInstance
from app.providers.openrouter import OpenRouterProvider
from app.providers.registry import PROVIDER_REGISTRY, ProviderSpec, spec_of

__all__ = [
    "PROVIDER_IDS",
    "PROVIDER_REGISTRY",
    "AnthropicProvider",
    "BaseProvider",
    "GeminiProvider",
    "GroqProvider",
    "LMStudioProvider",
    "ModelInfo",
    "NvidiaNimProvider",
    "OllamaProvider",
    "OpenAICompatibleProviderInstance",
    "OpenAIProvider",
    "OpenRouterProvider",
    "ProviderSpec",
    "ValidationResult",
    "create_provider",
    "get_configured_provider_ids",
    "spec_of",
]


def create_provider(
    provider_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    timeout: float = 30.0,
) -> BaseProvider:
    if provider_id == "gemini":
        return GeminiProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    elif provider_id == "openai":
        return OpenAIProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    elif provider_id == "anthropic":
        return AnthropicProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    elif provider_id == "groq":
        return GroqProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    elif provider_id == "openrouter":
        return OpenRouterProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    elif provider_id == "nvidia_nim":
        return NvidiaNimProvider(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    elif provider_id == "openai_compatible":
        return OpenAICompatibleProviderInstance(
            api_key=api_key, base_url=base_url, default_model=default_model, timeout=timeout
        )
    elif provider_id == "ollama":
        return OllamaProvider(base_url=base_url, default_model=default_model, timeout=timeout)
    elif provider_id == "lm_studio":
        return LMStudioProvider(base_url=base_url, default_model=default_model, timeout=timeout)
    else:
        raise ValueError(f"Unknown provider ID: {provider_id}")
