import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 1. Mock the optional ollama library before import
mock_ollama = MagicMock()
mock_ollama.chat = AsyncMock()


class MockOllamaStream:
    def __init__(self):
        self.chunks = [
            {"message": {"content": "Ollama "}},
            {"message": {"content": "stream content"}},
        ]
        self.idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.idx < len(self.chunks):
            res = self.chunks[self.idx]
            self.idx += 1
            return res
        raise StopAsyncIteration


async def ollama_chat_mock(*args, **kwargs):
    if kwargs.get("stream"):
        return MockOllamaStream()
    return {"message": {"content": "Ollama response content"}}


mock_ollama.chat.side_effect = ollama_chat_mock
sys.modules["ollama"] = mock_ollama

from app.search.llm_client import LLMClient  # noqa: E402


# Context manager for httpx.AsyncClient.stream
class AsyncContextManagerMock:
    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def patch_data_paths(tmp_path):
    fake_creds = tmp_path / "credentials.json"
    fake_settings = tmp_path / "settings.json"
    fake_prompt = tmp_path / "rag_system.txt"

    with patch(
        "app.search.llm_client.Path",
        lambda *args: {
            "data/credentials.json": fake_creds,
            "data/settings.json": fake_settings,
            "prompts/rag_system.txt": fake_prompt,
        }.get(os.path.join(*args).replace("\\", "/"), Path(*args)),
    ):
        yield {"credentials": fake_creds, "settings": fake_settings, "prompt": fake_prompt}


@pytest.mark.asyncio
async def test_llm_client_ensure_token_loaded(patch_data_paths):
    client = LLMClient()

    # Mock methods called in ensure_token_loaded
    client._load_oauth_token = MagicMock(return_value="mock_oauth")
    client._load_runtime_preferences = MagicMock()
    client._load_keyring_keys = AsyncMock()

    await client._ensure_token_loaded()
    assert client._oauth_token == "mock_oauth"  # noqa: S105
    assert client._token_loaded is True


@pytest.mark.asyncio
async def test_llm_client_load_keyring_keys():
    client = LLMClient()

    with patch("app.search.llm_client.keyring.get_password") as mock_get:
        # Mock successful fetches
        def get_pwd(service, username):
            if username == "gemini":
                return "gemini_key"
            elif username == "groq":
                return "groq_key"
            return None

        mock_get.side_effect = get_pwd

        await client._load_keyring_keys()
        assert client.api_key == "gemini_key"
        assert client.provider_keys["gemini"] == "gemini_key"
        assert client.provider_keys["groq"] == "groq_key"

    # Keyring error handling
    with patch("app.search.llm_client.keyring.get_password", side_effect=Exception("Keyring fail")):
        # Should not crash, just log warning
        await client._load_keyring_keys()


@pytest.mark.asyncio
async def test_refresh_token_if_expired(tmp_path):
    client = LLMClient()
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh123"  # noqa: S105
    mock_creds.token = "refreshed_token"  # noqa: S105

    token_data = {"token": "old"}
    token_file = tmp_path / "creds.json"

    with patch("google.auth.transport.requests.Request", MagicMock()):
        refreshed = client._refresh_token_if_expired(mock_creds, token_data, token_file)
        assert refreshed is True
        assert token_data["token"] == "refreshed_token"  # noqa: S105
        assert token_file.exists()
        saved = json.loads(token_file.read_text())
        assert saved["token"] == "refreshed_token"  # noqa: S105

    # Valid creds path
    mock_creds.valid = True
    refreshed2 = client._refresh_token_if_expired(mock_creds, token_data, token_file)
    assert refreshed2 is False


@pytest.mark.asyncio
async def test_load_oauth_token(patch_data_paths):
    client = LLMClient()

    # Credentials file missing
    assert client._load_oauth_token() is None

    # Credentials file exists, valid
    patch_data_paths["credentials"].write_text(json.dumps({"token": "t1"}))
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds.token = "token_valid"  # noqa: S105

    with (
        patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_info",
            return_value=mock_creds,
        ),
        patch.object(client, "_refresh_token_if_expired", return_value=False),
    ):
        assert client._load_oauth_token() == "token_valid"

    # Corrupt credentials file raises exception
    patch_data_paths["credentials"].write_text("corrupted_json")
    assert client._load_oauth_token() is None


def test_get_model_class():
    client = LLMClient()

    client.provider_preference = "gemini"
    assert client.get_model_class() == "cloud"

    client.provider_preference = "ollama"
    client.ollama_model = "llama-3b-instruct"
    assert client.get_model_class() == "3b_local"

    client.ollama_model = "llama-7b-instruct"
    assert client.get_model_class() == "7b_local"

    client.ollama_model = "custom"
    assert client.get_model_class() == "7b_local"

    client.provider_preference = "lm_studio"
    client.lm_studio_model = "phi-mini"
    assert client.get_model_class() == "3b_local"

    client.lm_studio_model = "mistral-7b"
    assert client.get_model_class() == "7b_local"

    client.lm_studio_model = "custom"
    assert client.get_model_class() == "7b_local"

    client.provider_preference = "auto"
    client.api_key = "key"
    assert client.get_model_class() == "cloud"

    client.api_key = None
    client._oauth_token = "token"  # noqa: S105
    assert client.get_model_class() == "cloud"

    client._oauth_token = None
    client.ollama_model = "llama-3b"
    assert client.get_model_class() == "3b_local"

    client.ollama_model = "llama-7b"
    assert client.get_model_class() == "7b_local"


def test_load_runtime_preferences(patch_data_paths):
    client = LLMClient()

    # Missing file
    client._load_runtime_preferences()
    assert client.provider_preference == "auto"

    # Valid file
    patch_data_paths["settings"].write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "ollama",
                    "gemini_model": "gemini-1.5-pro",
                    "ollama_model": "mistral",
                    "lm_studio_model": "phi3",
                }
            }
        )
    )
    client._load_runtime_preferences()
    assert client.provider_preference == "ollama"
    assert client.model == "gemini-1.5-pro"
    assert client.ollama_model == "mistral"
    assert client.lm_studio_model == "phi3"

    # Corrupt settings file
    patch_data_paths["settings"].write_text("{corrupt}")
    client._load_runtime_preferences()  # Should not raise


def test_apply_preferences():
    client = LLMClient()
    client.apply_preferences(
        provider="lm_studio", gemini_model="g1", ollama_model="o1", lm_studio_model="l1"
    )
    assert client.provider_preference == "lm_studio"
    assert client.model == "g1"
    assert client.ollama_model == "o1"
    assert client.lm_studio_model == "l1"


def test_build_prompt(patch_data_paths):
    client = LLMClient()

    # 1. System prompt file missing, default template
    prompt = client._build_prompt("my query", "my context", mode="explain", supports_claims=True)
    assert "user_query" in prompt
    assert "claim sources=" in prompt
    assert "MODE INSTRUCTION (Explain)" in prompt

    # 2. System prompt file exists
    patch_data_paths["prompt"].write_text("Hello ### Context {context} ### {query}")
    prompt2 = client._build_prompt("my query", "my context", mode="verify", supports_claims=True)
    assert "claim sources=" in prompt2
    assert "MODE INSTRUCTION (Verify)" in prompt2

    # Mode challenge/distill/explore
    prompt_explore = client._build_prompt("q", "c", mode="explore")
    assert "MODE INSTRUCTION (Explore)" in prompt_explore

    prompt_distill = client._build_prompt("q", "c", mode="distill")
    assert "MODE INSTRUCTION (Distill)" in prompt_distill

    prompt_challenge = client._build_prompt("q", "c", mode="challenge")
    assert "MODE INSTRUCTION (Challenge)" in prompt_challenge


@pytest.mark.asyncio
async def test_check_ollama_health():
    client = LLMClient()
    client.ollama_url = "http://ollama"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        assert await client._check_ollama_health() is True

    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=Exception("Connection refused"))):
        assert await client._check_ollama_health() is False


@pytest.mark.asyncio
async def test_check_lm_studio_health():
    client = LLMClient()
    client.lm_studio_url = "http://lmstudio"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        assert await client._check_lm_studio_health() is True

    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=Exception("Connection refused"))):
        assert await client._check_lm_studio_health() is False


@pytest.mark.asyncio
async def test_generate_answer():
    client = LLMClient()
    client._ensure_token_loaded = AsyncMock()

    # Mock the underlying calls
    client._call_gemini = AsyncMock(return_value="gemini_ans")
    client._call_lm_studio = AsyncMock(return_value="lm_studio_ans")
    client._call_ollama = AsyncMock(return_value="ollama_ans")

    # Mock health checks
    client._check_lm_studio_health = AsyncMock(return_value=True)
    client._check_ollama_health = AsyncMock(return_value=True)

    # Gemini API key present
    client.api_key = "key"
    client.provider_preference = "gemini"
    ans = await client.generate_answer("q", "c", skip_capability_check=True)
    assert ans == "gemini_ans"

    # LM studio fallback
    client.api_key = None
    client._oauth_token = None
    client.provider_preference = "auto"
    ans = await client.generate_answer("q", "c", skip_capability_check=True)
    assert ans == "lm_studio_ans"

    # Ollama fallback
    client._check_lm_studio_health = AsyncMock(return_value=False)
    ans = await client.generate_answer("q", "c", skip_capability_check=True)
    assert ans == "ollama_ans"

    # None available
    client._check_ollama_health = AsyncMock(return_value=False)
    ans = await client.generate_answer("q", "c", skip_capability_check=True)
    assert "LLM unavailable" in ans


@pytest.mark.asyncio
async def test_call_gemini_success():
    client = LLMClient()
    client.api_key = "fake_gemini_key"
    client.model = "gemini-model"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Gemini text response"}]}}]
    }

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)) as mock_post:
        ans = await client._call_gemini("prompt")
        assert ans == "Gemini text response"
        mock_post.assert_called_once()
        headers = mock_post.call_args[1]["headers"]
        assert headers["x-goog-api-key"] == "fake_gemini_key"


@pytest.mark.asyncio
async def test_call_gemini_oauth_success():
    client = LLMClient()
    client._oauth_token = "oauth_token"  # noqa: S105
    client.model = "gemini-model"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "OAuth response"}]}}]
    }

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)) as mock_post:
        ans = await client._call_gemini("prompt")
        assert ans == "OAuth response"
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer oauth_token"


@pytest.mark.asyncio
async def test_call_gemini_api_error():
    client = LLMClient()
    client.api_key = "fake_gemini_key"

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Invalid request arguments"

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        ans = await client._call_gemini("prompt")
        assert "Gemini API error 400" in ans


@pytest.mark.asyncio
async def test_call_ollama():
    client = LLMClient()
    client.ollama_model = "mistral"

    # Successful call
    ans = await client._call_ollama("prompt", history=[{"role": "user", "content": "hi"}])
    assert ans == "Ollama response content"

    # Ollama library failure
    with patch("ollama.chat", AsyncMock(side_effect=Exception("Ollama offline"))):
        ans_fail = await client._call_ollama("prompt")
        assert ans_fail == "Ollama failed."


@pytest.mark.asyncio
async def test_call_lm_studio_success():
    client = LLMClient()
    client.lm_studio_url = "http://lmstudio"
    client.lm_studio_model = "phi3"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "LM Studio answer"}}]}

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        ans = await client._call_lm_studio("prompt")
        assert ans == "LM Studio answer"

    # Non-200
    mock_resp.status_code = 500
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        ans = await client._call_lm_studio("prompt")
        assert "LM Studio error 500" in ans

    # Exception
    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=Exception("error"))):
        ans = await client._call_lm_studio("prompt")
        assert ans == "LM Studio failed."


@pytest.mark.asyncio
async def test_stream_gemini_success():
    client = LLMClient()
    client.api_key = "key"
    client.model = "gemini-model"

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_aiter_text():
        # JSON chunks corresponding to Gemini SSE stream
        yield "[\n"
        yield "  {\n"
        yield '    "candidates": [{"content": {"parts": [{"text": "Hello "}]}}]\n'
        yield "  },\n"
        yield "  {\n"
        yield '    "candidates": [{"content": {"parts": [{"text": "world!"}]}}]\n'
        yield "  }\n"
        yield "]\n"

    mock_resp.aiter_text = fake_aiter_text

    with patch("httpx.AsyncClient.stream", return_value=AsyncContextManagerMock(mock_resp)):
        results = []
        async for chunk in client._stream_gemini("prompt"):
            results.append(chunk)
        assert results == ["Hello ", "world!"]


@pytest.mark.asyncio
async def test_stream_gemini_error():
    client = LLMClient()
    client.api_key = "key"

    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch("httpx.AsyncClient.stream", return_value=AsyncContextManagerMock(mock_resp)):
        results = []
        async for chunk in client._stream_gemini("prompt"):
            results.append(chunk)
        assert results == ["Error: 403"]

    # Stream exception
    with patch("httpx.AsyncClient.stream", side_effect=Exception("Stream break")):
        results = []
        async for chunk in client._stream_gemini("prompt"):
            results.append(chunk)
        assert results == ["Streaming error."]


@pytest.mark.asyncio
async def test_stream_ollama():
    client = LLMClient()
    client.ollama_model = "mistral"

    results = []
    async for chunk in client._stream_ollama("prompt"):
        results.append(chunk)
    assert results == ["Ollama ", "stream content"]

    # Error
    with patch("ollama.chat", AsyncMock(side_effect=Exception("stream fail"))):
        results_fail = []
        async for chunk in client._stream_ollama("prompt"):
            results_fail.append(chunk)
        assert results_fail == ["Ollama stream failed."]


@pytest.mark.asyncio
async def test_stream_lm_studio_success():
    client = LLMClient()
    client.lm_studio_url = "http://lmstudio"
    client.lm_studio_model = "phi3"

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_aiter_lines():
        yield 'data: {"choices": [{"delta": {"content": "Stream "}}]}'
        yield 'data: {"choices": [{"delta": {"content": "chunk"}}]}'
        # Corrupt data check
        yield "data: {invalid_json}"
        yield "data: [DONE]"

    mock_resp.aiter_lines = fake_aiter_lines

    with patch("httpx.AsyncClient.stream", return_value=AsyncContextManagerMock(mock_resp)):
        results = []
        async for chunk in client._stream_lm_studio("prompt"):
            results.append(chunk)
        assert results == ["Stream ", "chunk"]


@pytest.mark.asyncio
async def test_stream_lm_studio_error():
    client = LLMClient()

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("httpx.AsyncClient.stream", return_value=AsyncContextManagerMock(mock_resp)):
        results = []
        async for chunk in client._stream_lm_studio("prompt"):
            results.append(chunk)
        assert results == ["LM Studio error 500"]

    # Exception
    with patch("httpx.AsyncClient.stream", side_effect=Exception("Stream crash")):
        results = []
        async for chunk in client._stream_lm_studio("prompt"):
            results.append(chunk)
        assert results == ["LM Studio stream failed."]


@pytest.mark.asyncio
async def test_stream_answer_monitoring():
    client = LLMClient()
    client._ensure_token_loaded = AsyncMock()

    # 1. Capability check passes, stream returns claims successfully
    with (
        patch(
            "app.search.llm_client.capability_detector.detect_capabilities",
            AsyncMock(return_value=True),
        ),
        patch.object(client, "_stream_gemini") as mock_stream,
    ):

        async def fake_stream(*args, **kwargs):
            yield '<claim sources="[1]">Fact</claim>'

        mock_stream.side_effect = fake_stream

        client.api_key = "key"
        client.provider_preference = "gemini"

        results = []
        async for chunk in client.stream_answer("q", "c"):
            results.append(chunk)
        assert results == ['<claim sources="[1]">Fact</claim>']

    # 2. Capability check passes, but stream fails to return `<claim` within 600 chars
    with (
        patch(
            "app.search.llm_client.capability_detector.detect_capabilities",
            AsyncMock(return_value=True),
        ),
        patch("app.search.llm_client.capability_detector.report_failure") as mock_report,
        patch.object(client, "_stream_gemini") as mock_stream,
    ):

        async def fake_stream_no_claims(*args, **kwargs):
            # yield 700 chars of text without <claim
            yield "A" * 700

        mock_stream.side_effect = fake_stream_no_claims

        results = []
        async for chunk in client.stream_answer("q", "c"):
            results.append(chunk)

        mock_report.assert_called_once_with(client)
