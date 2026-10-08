"""A9-04: cl100k_base must load from the bundled BPE file, never from the network."""

import logging

import pytest
import tiktoken
import tiktoken.load
import tiktoken.registry

import app.search.context_builder as cb

SAMPLE = "Curl noise turbulence is applied to the velocity field."


@pytest.fixture
def cold(monkeypatch, tmp_path):
    """Fresh module + tiktoken state, an empty user cache dir, and no network."""
    fetched: list[str] = []

    def no_network(blobpath: str) -> bytes:
        fetched.append(blobpath)
        raise AssertionError(f"network fetch attempted: {blobpath}")

    monkeypatch.setattr(tiktoken.load, "read_file", no_network)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "user_cache"))
    monkeypatch.setattr(cb, "_ENCODING", None)
    cb._get_tokens.cache_clear()
    yield fetched
    monkeypatch.setattr(cb, "_ENCODING", None)
    cb._get_tokens.cache_clear()


def test_bundled_asset_loads_offline_with_identical_token_counts(cold):
    assert cb._get_encoding() is not False
    # Counts measured with the downloaded file before the asset was bundled.
    assert cb._token_count("hello") == 1
    assert cb._token_count(SAMPLE) == 11
    assert cb._token_count("def f(x):\n    return x**2  # ünïcode ✓" * 5) == 80
    assert cb._token_count(SAMPLE) != max(1, len(SAMPLE) // 4)
    assert cold == []


def test_missing_asset_falls_back_to_char_heuristic_and_never_fetches(
    cold, monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(cb, "_BUNDLED_BPE_DIR", tmp_path / "empty")
    with caplog.at_level(logging.WARNING, logger=cb.logger.name):
        assert cb._get_encoding() is False
        assert cb._get_encoding() is False
    assert cb._token_count(SAMPLE) == max(1, len(SAMPLE) // 4)
    assert cold == []
    assert len([r for r in caplog.records if "len(text)//4" in r.getMessage()]) == 1


def test_corrupt_asset_is_refused_not_handed_to_tiktoken(cold, monkeypatch, tmp_path):
    """tiktoken deletes a cache file that fails its hash and then fetches."""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / cb._BPE_CACHE_KEY).write_bytes(b"not the bpe file\r\n")
    monkeypatch.setattr(cb, "_BUNDLED_BPE_DIR", bad)
    assert cb._get_encoding() is False
    assert cold == []
    assert (bad / cb._BPE_CACHE_KEY).exists()
