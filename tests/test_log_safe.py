"""Logged values must not be able to forge a log entry with CR/LF."""

import logging

from app.providers import ollama
from app.utils.log_safe import log_safe


def test_log_safe_escapes_line_breaks():
    assert log_safe("a\nb\rc") == "a\\nb\\rc"
    assert log_safe(["x\ny"]) == "['x\\ny']"
    assert log_safe(8192) == "8192"


def test_ollama_log_line_cannot_be_split_by_a_model_name(caplog):
    forged = "gemma\nERROR forged entry"
    with caplog.at_level(logging.INFO, logger=ollama.__name__):
        ollama._resolve_num_ctx("http://log-safe.test", forged, 8192)
    messages = [r.getMessage() for r in caplog.records if "high-water" in r.getMessage()]
    assert messages, "the high-water line was not logged"
    assert all("\n" not in m for m in messages)
