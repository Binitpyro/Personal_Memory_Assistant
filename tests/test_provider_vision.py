"""Vision message shaping. No network, no provider instances."""

import base64
import json

import pytest

from app.providers.vision import (
    UnsupportedVisionProviderError,
    build_vision_messages,
    supports_vision_messages,
)

PNG = b"\x89PNG\r\n\x1a\n-not-really-but-opaque-bytes"


def test_ollama_puts_the_image_in_a_sibling_key():
    """Ollama keeps `content` a string and adds `images`."""
    messages = build_vision_messages("ollama", "Read this page.", PNG)

    assert len(messages) == 1
    msg = messages[0]
    assert msg["role"] == "user"
    assert msg["content"] == "Read this page."
    assert msg["images"] == [base64.b64encode(PNG).decode("ascii")]


def test_openai_style_replaces_content_with_parts():
    """LM Studio speaks the OpenAI dialect, where `content` becomes a list."""
    messages = build_vision_messages("lm_studio", "Read this page.", PNG)

    parts = messages[0]["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "Read this page."}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64encode(PNG).decode("ascii") in parts[1]["image_url"]["url"]


def test_both_shapes_survive_json_serialization():
    """Providers post `messages` verbatim, so it has to be JSON already."""
    for pid in ("ollama", "lm_studio"):
        json.dumps(build_vision_messages(pid, "x", PNG))


def test_an_unknown_provider_raises_instead_of_dropping_the_image():
    """Sending the prompt without the image would look like a working OCR run.

    The model would describe nothing, and that output would be cached and
    indexed as if it were the document's text.
    """
    assert not supports_vision_messages("anthropic")
    with pytest.raises(UnsupportedVisionProviderError):
        build_vision_messages("anthropic", "Read this page.", PNG)


def test_an_empty_image_is_refused():
    with pytest.raises(ValueError):
        build_vision_messages("ollama", "Read this page.", b"")


def test_vision_detection_covers_the_models_we_suggest():
    """Anything we tell the user to install must then be recognised.

    Suggesting a model the picker would immediately flag as text-only would be
    self-contradictory.
    """
    from app.providers.vision import SUGGESTED_VISION_MODELS, looks_like_vision_model

    for name in SUGGESTED_VISION_MODELS:
        assert looks_like_vision_model(name), name
        # Real tags carry a size/quant suffix.
        assert looks_like_vision_model(f"{name}:latest"), name


def test_vision_detection_does_not_flag_ordinary_text_models():
    from app.providers.vision import looks_like_vision_model

    for name in ("llama3:8b", "mistral", "phi3:mini", "qwen2.5-coder:7b", "deepseek-r1"):
        assert not looks_like_vision_model(name), name


def test_mime_type_is_honoured():
    messages = build_vision_messages("lm_studio", "x", PNG, mime_type="image/jpeg")
    assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
