import keyring
from app.config import settings
from app.providers.registry import PROVIDER_REGISTRY

PROVIDER_IDS = list(PROVIDER_REGISTRY.keys())


def get_configured_provider_ids() -> list[str]:
    """
    Dynamically returns all currently valid and configured provider IDs.
    Inspects environment/settings keys, OS keyring, and active local servers.
    """
    configured = []
    # Check cloud / non-local providers first
    for pid in PROVIDER_IDS:
        if pid in ("openai_compatible", "ollama", "lm_studio"):
            continue
        env_key_name = f"{pid}_api_key"
        if getattr(settings, env_key_name, None):
            configured.append(pid)
            continue
        try:
            key = keyring.get_password("pma_backend", pid)
            if key:
                configured.append(pid)
        except Exception:
            pass

    # Check local providers in priority order: lm_studio, ollama
    for pid in ("lm_studio", "ollama"):
        if pid in PROVIDER_IDS:
            configured.append(pid)

    return configured

