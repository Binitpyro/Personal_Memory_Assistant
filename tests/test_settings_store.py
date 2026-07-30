import json
import pytest

from app.settings_store import CURRENT_SCHEMA_VERSION, SettingsStore, SETTINGS_PATH


def test_settings_store_save_and_read(tmp_path, monkeypatch):
    test_path = tmp_path / "settings.json"
    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", test_path)

    data = {"llm": {"provider": "ollama", "fallback_chain": ["ollama"]}}
    SettingsStore.save(data)

    assert test_path.exists()
    read_data = SettingsStore.read()
    assert read_data["schema_version"] == CURRENT_SCHEMA_VERSION
    assert read_data["llm"]["provider"] == "ollama"
    assert read_data["llm"]["fallback_chain"] == ["ollama"]


def test_settings_store_read_missing(tmp_path, monkeypatch):
    test_path = tmp_path / "nonexistent.json"
    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", test_path)

    data = SettingsStore.read()
    assert data == {}


def test_settings_store_read_corrupt_raises(tmp_path, monkeypatch):
    test_path = tmp_path / "corrupt.json"
    test_path.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", test_path)

    with pytest.raises(json.JSONDecodeError):
        SettingsStore.read()
