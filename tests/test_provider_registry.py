import re
from app.providers.registry import PROVIDER_REGISTRY


def test_provider_registry_api_key_patterns():
    for pid, spec in PROVIDER_REGISTRY.items():
        if spec.api_key_pattern:
            # Verify it compiles as a valid regex
            try:
                re.compile(spec.api_key_pattern)
            except re.error as e:
                assert False, f"api_key_pattern for provider {pid} fails to compile: {e}"


def test_provider_registry_base_url():
    for pid, spec in PROVIDER_REGISTRY.items():
        # Every provider spec has a non-empty default_base_url or base_url_editable=True
        assert spec.default_base_url is not None or spec.base_url_editable, (
            f"Provider {pid} must have default_base_url or be base_url_editable"
        )
