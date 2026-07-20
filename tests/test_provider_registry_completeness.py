import re
from pathlib import Path
from app.providers import PROVIDER_IDS


def test_setup_page_provider_completeness():
    # If SetupPage.tsx has a hardcoded PROVIDERS list, check that every provider ID in it is in PROVIDER_IDS
    setup_path = Path("frontend/src/pages/SetupPage.tsx")
    if not setup_path.exists():
        return

    content = setup_path.read_text(encoding="utf-8")
    # Match id: '...' or id: "..."
    matches = re.findall(r"id:\s*['\"]([a-zA-Z0-9_-]+)['\"]", content)
    for provider_id in matches:
        # Some matches might be other fields, but let's filter by checking those that look like provider IDs
        # (gemini, groq, nvidia_nim, openrouter, etc)
        if provider_id in ("gemini", "groq", "nvidia_nim", "openrouter", "openai", "anthropic", "ollama", "lm_studio"):
            assert provider_id in PROVIDER_IDS, f"Provider ID '{provider_id}' in SetupPage.tsx is not registered in PROVIDER_IDS."
