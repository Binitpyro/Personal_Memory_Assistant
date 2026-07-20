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
    models_parser: str            # "openai" | "anthropic" | "gemini" | "ollama_tags"
    api_key_pattern: str | None   # regex sanity check
    api_key_docs_url: str
    supports_streaming: bool
    supports_tools: bool
    supports_vision: bool
    supported_features: set[str]  # "reasoning", "system_prompt", "json_mode", ...


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        id="gemini",
        display_name="Gemini",
        kind="cloud",
        default_base_url="https://generativelanguage.googleapis.com",
        base_url_editable=False,
        auth="x-goog-api-key",
        models_endpoint="/v1beta/models",
        models_parser="gemini",
        api_key_pattern=r"^AIza[0-9A-Za-z_-]{35}$",
        api_key_docs_url="https://aistudio.google.com/app/apikey",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
    "openai": ProviderSpec(
        id="openai",
        display_name="OpenAI",
        kind="cloud",
        default_base_url="https://api.openai.com/v1",
        base_url_editable=False,
        auth="bearer",
        models_endpoint="/models",
        models_parser="openai",
        api_key_pattern=r"^sk-[A-Za-z0-9_-]{20,}$",
        api_key_docs_url="https://platform.openai.com/api-keys",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        display_name="Anthropic",
        kind="cloud",
        default_base_url="https://api.anthropic.com/v1",
        base_url_editable=False,
        auth="x-api-key",
        models_endpoint="/v1/models",
        models_parser="anthropic",
        api_key_pattern=r"^sk-ant-[A-Za-z0-9_-]{20,}$",
        api_key_docs_url="https://console.anthropic.com/settings/keys",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt"},
    ),
    "groq": ProviderSpec(
        id="groq",
        display_name="Groq",
        kind="cloud",
        default_base_url="https://api.groq.com/openai/v1",
        base_url_editable=False,
        auth="bearer",
        models_endpoint="/models",
        models_parser="openai",
        api_key_pattern=r"^gsk_[A-Za-z0-9]{20,}$",
        api_key_docs_url="https://console.groq.com/keys",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=False,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        display_name="OpenRouter",
        kind="aggregator",
        default_base_url="https://openrouter.ai/api/v1",
        base_url_editable=False,
        auth="bearer",
        models_endpoint="/models",
        models_parser="openai",
        api_key_pattern=r"^sk-or-v1-[A-Za-z0-9]+$",
        api_key_docs_url="https://openrouter.ai/keys",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
    "nvidia_nim": ProviderSpec(
        id="nvidia_nim",
        display_name="NVIDIA NIM",
        kind="cloud",
        default_base_url="https://integrate.api.nvidia.com/v1",
        base_url_editable=True,
        auth="bearer",
        models_endpoint="/models",
        models_parser="openai",
        api_key_pattern=r"^nvapi-[A-Za-z0-9_-]{20,}$",
        api_key_docs_url="https://build.nvidia.com/settings/api-keys",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
    "ollama": ProviderSpec(
        id="ollama",
        display_name="Ollama",
        kind="local",
        default_base_url="http://localhost:11434",
        base_url_editable=True,
        auth="none",
        models_endpoint="/api/tags",
        models_parser="ollama_tags",
        api_key_pattern=None,
        api_key_docs_url="https://ollama.com",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
    "lm_studio": ProviderSpec(
        id="lm_studio",
        display_name="LM Studio",
        kind="local",
        default_base_url="http://localhost:1234/v1",
        base_url_editable=True,
        auth="none",
        models_endpoint="/models",
        models_parser="openai",
        api_key_pattern=None,
        api_key_docs_url="https://lmstudio.ai",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
    "openai_compatible": ProviderSpec(
        id="openai_compatible",
        display_name="OpenAI-Compatible",
        kind="custom",
        default_base_url=None,
        base_url_editable=True,
        auth="bearer",
        models_endpoint="/models",
        models_parser="openai",
        api_key_pattern=None,
        api_key_docs_url="",
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supported_features={"reasoning", "system_prompt", "json_mode"},
    ),
}


def spec_of(provider_id: str) -> ProviderSpec:
    if provider_id not in PROVIDER_REGISTRY:
        raise ValueError(f"Unknown provider ID: {provider_id}")
    return PROVIDER_REGISTRY[provider_id]
