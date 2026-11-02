"""POST /api/ocr/uninstall must never rmtree outside the OCR root (A5-02 / A6-01)."""

import pytest

from app.config import settings
from app.ocr import registry
from app.ocr import settings as ocr_settings


@pytest.fixture
def ocr_tree(monkeypatch, tmp_path):
    """A tmp OCR root next to a sentinel directory that must never be deleted."""
    root = tmp_path / "ocr"
    root.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious")
    monkeypatch.setattr(ocr_settings, "ocr_root", lambda: root)
    monkeypatch.setattr(registry, "ocr_root", lambda: root)
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(settings, "ocr_tier", "none")
    return root, victim


@pytest.mark.parametrize("kind", ["absolute", "traversal"])
async def test_uninstall_refuses_escaping_tier(client, ocr_tree, kind):
    root, victim = ocr_tree
    tier = str(victim) if kind == "absolute" else "../../victim"

    res = await client.post("/api/ocr/uninstall", json={"tier": tier})

    assert res.status_code == 400
    assert (victim / "keep.txt").read_text() == "precious"
    assert root.exists()


async def test_uninstall_refuses_traversal_straight_to_registry(ocr_tree):
    """Defence in depth: even a tier that slipped past the allow-list cannot escape."""
    _root, victim = ocr_tree
    registry.TIER_DEPS["../../victim"] = registry.TIER_DEPS["cpu"]
    try:
        result = await registry.uninstall_tier("../../victim")
    finally:
        del registry.TIER_DEPS["../../victim"]

    assert result["ok"] is False
    assert (victim / "keep.txt").exists()


async def test_uninstall_valid_tier_still_works(client, ocr_tree):
    root, victim = ocr_tree
    (root / "env_cpu").mkdir()
    (root / "models" / "cpu").mkdir(parents=True)

    res = await client.post("/api/ocr/uninstall", json={"tier": "cpu"})

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert not (root / "env_cpu").exists()
    assert not (root / "models" / "cpu").exists()
    assert (victim / "keep.txt").exists()


async def test_empty_body_uninstall_with_vlm_active_still_resets(client, ocr_tree, monkeypatch):
    """The settings fallback ("vlm"/"none") owns no venv but must still reach the reset block."""
    monkeypatch.setattr(settings, "ocr_tier", "vlm")
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr("app.ocr.api.persist_enabled", lambda *_: None)
    monkeypatch.setattr(ocr_settings, "persist_active_tier", lambda *_: None)

    res = await client.post("/api/ocr/uninstall")

    assert res.status_code == 200
    assert res.json() == {"ok": True, "removed": []}
    assert settings.ocr_tier == "none"
    assert settings.ocr_enabled is False

    res = await client.post("/api/ocr/uninstall", json={"tier": "../.."})
    assert res.status_code == 400
