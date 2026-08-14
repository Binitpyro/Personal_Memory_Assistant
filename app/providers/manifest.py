import ipaddress
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


def is_local_endpoint_reachable(url: str, timeout: float = 0.2, use_cache: bool = True) -> bool:
    """Fast socket check (0.2s) with 5s TTL cache to verify local service availability.

    Pass use_cache=False to force a fresh probe. The result is still written to the
    cache, so a service that just came up is immediately visible to every other caller
    (see app/providers/launcher.py, which polls a provider it has just started).
    """
    now = time.monotonic()
    if use_cache and url in _reachability_cache:
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


#: Hosts that are unambiguously this machine. Mirrors `main._LOOPBACK_HOSTS`.
#: 0.0.0.0 is deliberately absent: as a *destination* it is ambiguous, and an
#: ambiguous destination should prompt for consent rather than skip the gate.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})


def is_loopback_url(url: str | None) -> bool:
    """True when *url* addresses this machine.

    Provider *kind* says what a provider usually is, not where this install
    actually points it. `ollama` is registered as kind="local", but its base URL
    is a free-text setting - so a user (or a stale .env) can aim a "local"
    provider at another host, and the privacy consent gate would never fire.
    Whether data leaves the device is a property of the destination, so that is
    what gets checked.

    Unparseable or empty is treated as *not* loopback: the safe default for a
    gate is to ask.
    """
    if not url:
        return False
    try:
        host = urlparse(url if "//" in url else f"//{url}").hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.strip("[]").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    # 127.0.0.0/8 is all loopback, not just 127.0.0.1.
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def env_base_url(provider_id: str) -> str | None:
    """Base URL from .env for *provider_id*, if any.

    Settings uses two naming shapes: cloud providers carry `<id>_base_url`
    (openai_base_url, openai_compatible_base_url, nvidia_nim_base_url), local
    ones carry `<id>_url` (ollama_url, lm_studio_url). Check both.
    """
    for attr in (f"{provider_id}_base_url", f"{provider_id}_url"):
        value = getattr(settings, attr, None)
        if value:
            return str(value)
    return None


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
