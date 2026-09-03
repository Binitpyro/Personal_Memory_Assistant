from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    display_name: str
    kind: Literal["cloud", "local", "aggregator", "custom"]
    default_base_url: str | None
    base_url_editable: bool
    auth: Literal["bearer", "x-api-key", "x-goog-api-key", "none"]
    models_endpoint: str
    api_key_pattern: str | None  # regex sanity check
    api_key_docs_url: str


DEFAULT_CHAIN_ORDER: tuple[str, ...] = (
    "ollama",
    "lm_studio",
    "openai_compatible",
    "anthropic",
    "openai",
    "groq",
    "nvidia_nim",
    "openrouter",
    "gemini",
)


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        id="gemini",
        display_name="Gemini",
        kind="cloud",
        default_base_url="https://generativelanguage.googleapis.com",
        base_url_editable=False,
        auth="x-goog-api-key",
        models_endpoint="/v1beta/models",
        api_key_pattern=r"^AIza[0-9A-Za-z_-]{35}$",
        api_key_docs_url="https://aistudio.google.com/app/apikey",
    ),
    "openai": ProviderSpec(
        id="openai",
        display_name="OpenAI",
        kind="cloud",
        default_base_url="https://api.openai.com/v1",
        base_url_editable=False,
        auth="bearer",
        models_endpoint="/models",
        api_key_pattern=r"^sk-[A-Za-z0-9_-]{20,}$",
        api_key_docs_url="https://platform.openai.com/api-keys",
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        display_name="Anthropic",
        kind="cloud",
        default_base_url="https://api.anthropic.com",
        base_url_editable=False,
        auth="x-api-key",
        models_endpoint="/v1/models",
        api_key_pattern=r"^sk-ant-[A-Za-z0-9_-]{20,}$",
        api_key_docs_url="https://console.anthropic.com/settings/keys",
    ),
    "groq": ProviderSpec(
        id="groq",
        display_name="Groq",
        kind="cloud",
        default_base_url="https://api.groq.com/openai/v1",
        base_url_editable=False,
        auth="bearer",
        models_endpoint="/models",
        api_key_pattern=r"^gsk_[A-Za-z0-9]{20,}$",
        api_key_docs_url="https://console.groq.com/keys",
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        display_name="OpenRouter",
        kind="aggregator",
        default_base_url="https://openrouter.ai/api/v1",
        base_url_editable=False,
        auth="bearer",
        models_endpoint="/models",
        api_key_pattern=r"^sk-or-v1-[A-Za-z0-9]+$",
        api_key_docs_url="https://openrouter.ai/keys",
    ),
    "nvidia_nim": ProviderSpec(
        id="nvidia_nim",
        display_name="NVIDIA NIM",
        kind="cloud",
        default_base_url="https://integrate.api.nvidia.com/v1",
        base_url_editable=True,
        auth="bearer",
        models_endpoint="/models",
        api_key_pattern=r"^nvapi-[A-Za-z0-9_-]{20,}$",
        api_key_docs_url="https://build.nvidia.com/settings/api-keys",
    ),
    "ollama": ProviderSpec(
        id="ollama",
        display_name="Ollama",
        kind="local",
        default_base_url="http://localhost:11434",
        base_url_editable=True,
        auth="none",
        models_endpoint="/api/tags",
        api_key_pattern=None,
        api_key_docs_url="https://ollama.com",
    ),
    "lm_studio": ProviderSpec(
        id="lm_studio",
        display_name="LM Studio",
        kind="local",
        default_base_url="http://localhost:1234/v1",
        base_url_editable=True,
        auth="none",
        models_endpoint="/models",
        api_key_pattern=None,
        api_key_docs_url="https://lmstudio.ai",
    ),
    "openai_compatible": ProviderSpec(
        id="openai_compatible",
        display_name="OpenAI-Compatible",
        kind="custom",
        default_base_url=None,
        base_url_editable=True,
        auth="bearer",
        models_endpoint="/models",
        api_key_pattern=None,
        api_key_docs_url="",
    ),
}


def spec_of(provider_id: str) -> ProviderSpec:
    if provider_id not in PROVIDER_REGISTRY:
        raise ValueError(f"Unknown provider ID: {provider_id}")
    return PROVIDER_REGISTRY[provider_id]
