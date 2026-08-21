"""
tests/test_provider_manifest.py
Coverage for app/providers/manifest.py:
- Socket reachability checks (success & failure)
- 5s TTL cache behavior
- clear_reachability_cache helper
- Async provider resolution helpers
"""

import socket
from unittest.mock import patch

import pytest

from app.providers import manifest


def test_reachability_closed_port():
    manifest.clear_reachability_cache()
    # Port 59999 should not be listening on localhost
    result = manifest.is_local_endpoint_reachable("http://127.0.0.1:59999", timeout=0.05)
    assert result is False


def test_reachability_open_port():
    manifest.clear_reachability_cache()
    # Create a temporary local socket listening on an open port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    try:
        url = f"http://127.0.0.1:{port}"
        result = manifest.is_local_endpoint_reachable(url, timeout=0.2)
        assert result is True
    finally:
        sock.close()


def test_reachability_ttl_cache():
    manifest.clear_reachability_cache()
    url = "http://127.0.0.1:58888"

    # First call - port closed -> False
    res1 = manifest.is_local_endpoint_reachable(url, timeout=0.05)
    assert res1 is False
    assert url in manifest._reachability_cache

    # Subsequent call within TTL should return cached result instantly without socket probe
    with patch("socket.create_connection", side_effect=Exception("Should not be called!")):
        res2 = manifest.is_local_endpoint_reachable(url, timeout=0.05)
        assert res2 is False

    # Clear cache
    manifest.clear_reachability_cache()
    assert url not in manifest._reachability_cache


@pytest.mark.asyncio
async def test_async_helpers():
    ids = await manifest.get_configured_provider_ids_async()
    assert isinstance(ids, list)

    chain = await manifest.get_default_chain_async()
    assert isinstance(chain, list)


def test_default_chain_order_is_local_first_cloud_last():
    """Pins the privacy invariant: local providers are tried before any cloud
    provider, and gemini (which persisted queries on read until that was fixed)
    is last. Nothing else asserted on DEFAULT_CHAIN_ORDER's contents/order
    before this test - reordering it silently would previously pass CI."""
    from app.providers.registry import DEFAULT_CHAIN_ORDER

    assert DEFAULT_CHAIN_ORDER[0] == "ollama"
    assert DEFAULT_CHAIN_ORDER[1] == "lm_studio"
    assert DEFAULT_CHAIN_ORDER[-1] == "gemini"
    assert DEFAULT_CHAIN_ORDER.index("ollama") < DEFAULT_CHAIN_ORDER.index("gemini")


def test_get_default_chain_preserves_local_first_order():
    """get_default_chain() must preserve DEFAULT_CHAIN_ORDER, not the order
    returned by get_configured_provider_ids(). Patches out the reachability
    probe so this doesn't depend on whether Ollama/LM Studio happen to be
    running on the machine executing the test."""
    with patch(
        "app.providers.manifest.get_configured_provider_ids",
        return_value=["gemini", "ollama", "lm_studio"],
    ):
        chain = manifest.get_default_chain()
    assert chain == ["ollama", "lm_studio", "gemini"]
