import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.config import settings
from app.providers import PROVIDER_IDS, spec_of
from app.providers.registry import DEFAULT_CHAIN_ORDER


def test_setup_page_provider_completeness():
    setup_path = Path("frontend/src/pages/SetupPage.tsx")
    if not setup_path.exists():
        return

    content = setup_path.read_text(encoding="utf-8")
    matches = re.findall(r"id:\s*['\"]([a-zA-Z0-9_-]+)['\"]", content)
    for provider_id in matches:
        if provider_id in (
            "gemini",
            "groq",
            "nvidia_nim",
            "openrouter",
            "openai",
            "anthropic",
            "ollama",
            "lm_studio",
        ):
            assert provider_id in PROVIDER_IDS, (
                f"Provider ID '{provider_id}' in SetupPage.tsx is not registered in PROVIDER_IDS."
            )


def test_every_credentialled_provider_has_a_settings_field():
    for pid in PROVIDER_IDS:
        spec = spec_of(pid)
        if spec.auth == "none" or pid in ("openai_compatible", "ollama", "lm_studio"):
            continue
        assert hasattr(settings, f"{pid}_api_key"), f"{pid} missing settings field"


@pytest.mark.parametrize("pid", PROVIDER_IDS)
def test_no_duplicated_path_segment(pid):
    spec = spec_of(pid)
    if not spec.default_base_url:
        return
    url = spec.default_base_url.rstrip("/") + spec.models_endpoint
    segs = [s for s in urlparse(url).path.split("/") if s]
    # Check no segment appears twice consecutively
    for i in range(len(segs) - 1):
        assert segs[i] != segs[i + 1], f"Duplicated URL path segment in {pid}: {url}"


def test_default_chain_order_locals_first():
    ollama_idx = DEFAULT_CHAIN_ORDER.index("ollama")
    lm_studio_idx = DEFAULT_CHAIN_ORDER.index("lm_studio")
    gemini_idx = DEFAULT_CHAIN_ORDER.index("gemini")
    openai_idx = DEFAULT_CHAIN_ORDER.index("openai")

    assert ollama_idx < gemini_idx
    assert lm_studio_idx < gemini_idx
    assert ollama_idx < openai_idx
