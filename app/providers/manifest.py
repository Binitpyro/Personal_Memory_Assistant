import logging
import keyring

from app.config import settings
from app.providers.registry import DEFAULT_CHAIN_ORDER, PROVIDER_REGISTRY

logger = logging.getLogger(__name__)

PROVIDER_IDS = list(PROVIDER_REGISTRY.keys())


def get_configured_provider_ids() -> list[str]:
    """
    Dynamically returns all currently configured provider IDs (has API key or endpoint URL).
    Iterates according to DEFAULT_CHAIN_ORDER (local providers first).
    """
    configured = []

    for pid in DEFAULT_CHAIN_ORDER:
        if pid not in PROVIDER_REGISTRY:
            continue

        if pid in ("ollama", "lm_studio"):
            url = getattr(settings, f"{pid}_url", None)
            if url:
                configured.append(pid)
            continue

        if pid == "openai_compatible":
            base_url = getattr(settings, "openai_compatible_base_url", None)
            if base_url:
                configured.append(pid)
            continue

        env_key_name = f"{pid}_api_key"
        if getattr(settings, env_key_name, None):
            configured.append(pid)
            continue

        try:
            key = keyring.get_password("pma_backend", pid)
            if key:
                configured.append(pid)
        except Exception as e:
            logger.debug("Keyring lookup failed for %s: %s", pid, e)

    return configured


def get_default_chain() -> list[str]:
    """Returns default local-first chain order filtered by configured providers."""
    configured = set(get_configured_provider_ids())
    return [p for p in DEFAULT_CHAIN_ORDER if p in configured]
