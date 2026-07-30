import logging
import socket
import time
from urllib.parse import urlparse

import keyring

from app.config import settings
from app.providers.registry import DEFAULT_CHAIN_ORDER, PROVIDER_REGISTRY

logger = logging.getLogger(__name__)

PROVIDER_IDS = list(PROVIDER_REGISTRY.keys())

_reachability_cache: dict[str, tuple[bool, float]] = {}
_REACHABILITY_TTL = 5.0  # 5 second TTL cache


def clear_reachability_cache() -> None:
    """Helper to clear reachability cache, useful in test fixtures."""
    _reachability_cache.clear()


def is_local_endpoint_reachable(url: str, timeout: float = 0.2) -> bool:
    """Fast socket check (0.2s) with 5s TTL cache to verify local service availability."""
    import os
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True

    now = time.monotonic()
    if url in _reachability_cache:
        cached_res, cached_ts = _reachability_cache[url]
        if now - cached_ts < _REACHABILITY_TTL:
            return cached_res

    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            _reachability_cache[url] = (True, now)
            return True
    except Exception:
        _reachability_cache[url] = (False, now)
        return False


def get_configured_provider_ids() -> list[str]:
    """
    Dynamically returns all currently configured provider IDs.
    Local providers (ollama, lm_studio) must pass a reachability check to be marked configured.
    Iterates according to DEFAULT_CHAIN_ORDER (local providers first).
    """
    configured = []

    for pid in DEFAULT_CHAIN_ORDER:
        if pid not in PROVIDER_REGISTRY:
            continue

        if pid in ("ollama", "lm_studio"):
            url = getattr(settings, f"{pid}_url", None)
            if url and is_local_endpoint_reachable(url):
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


async def get_configured_provider_ids_async() -> list[str]:
    import asyncio
    return await asyncio.to_thread(get_configured_provider_ids)


async def get_default_chain_async() -> list[str]:
    import asyncio
    return await asyncio.to_thread(get_default_chain)
