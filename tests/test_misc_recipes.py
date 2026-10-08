"""A7-08: every Quick Start recipe's provider id and model must survive
POST /llm/preferences (it used to coerce anthropic/groq to 'auto' and drop the
model)."""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import models as m
from app.providers import PROVIDER_IDS
from app.settings_store import SettingsStore

_TSX = Path(__file__).resolve().parent.parent / "frontend/src/providers/ProviderRecipes.tsx"
_RECIPE = re.compile(
    r"fallback: \[([^\]]*)\],\s*defaultModel: \{ provider: '(\w+)', model: (null|'[^']+')"
)


def _recipes():
    out = []
    for chain, provider, model in _RECIPE.findall(_TSX.read_text(encoding="utf-8")):
        ids = re.findall(r"'(\w+)'", chain)
        out.append((provider, None if model == "null" else model.strip("'"), ids))
    return out


def test_recipes_parsed_and_ids_are_registry_ids():
    recipes = _recipes()
    assert len(recipes) == 3
    for provider, _model, chain in recipes:
        assert provider in PROVIDER_IDS
        assert set(chain) <= set(PROVIDER_IDS), chain


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", [(p, mdl or "some-model") for p, mdl, _ in _recipes()])
async def test_recipe_provider_and_model_round_trip(provider, model):
    payload = m.LLMPreferences(
        **{"provider": provider, f"{provider}_model": model, "cloud_privacy_consent": True}
    )
    with patch("app.api.models.get_llm", return_value=MagicMock()):
        await m.set_preferences(payload)
    llm = SettingsStore.read()["llm"]
    assert llm["provider"] == provider
    stored = llm.get(f"{provider}_model") or llm["per_provider"][provider]["default_model"]
    assert stored == model


@pytest.mark.asyncio
async def test_cloud_recipe_provider_needs_consent():
    payload = m.LLMPreferences(provider="anthropic", anthropic_model="x")
    with (
        patch("app.api.models.get_llm", return_value=MagicMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await m.set_preferences(payload)
    assert exc.value.status_code == 400
