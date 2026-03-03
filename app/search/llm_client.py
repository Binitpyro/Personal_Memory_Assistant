import logging
import httpx
import json
from typing import Optional, AsyncGenerator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import settings

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        self.api_key = settings.gemini_api_key
        self.ollama_url = settings.ollama_url
        self.ollama_model = settings.ollama_model
        self.model = settings.gemini_model
        self._gemini_client: Optional[httpx.AsyncClient] = None
        self._ollama_client: Optional[httpx.AsyncClient] = None

    def _get_gemini_client(self) -> httpx.AsyncClient:
        """Lazy-create a reusable async HTTP client for Gemini."""
        if self._gemini_client is None or self._gemini_client.is_closed:
            self._gemini_client = httpx.AsyncClient(timeout=settings.gemini_timeout)
        return self._gemini_client

    def _get_ollama_client(self) -> httpx.AsyncClient:
        """Lazy-create a reusable async HTTP client for Ollama."""
        if self._ollama_client is None or self._ollama_client.is_closed:
            self._ollama_client = httpx.AsyncClient(timeout=settings.ollama_timeout)
        return self._ollama_client

    def _build_prompt(self, query: str, context: str) -> str:
        return f"""
You are a personal memory assistant. Answer the user's question using ONLY the provided context snippets.
If the answer is not in the context, say "I don't have enough information in your indexed files to answer this."

Context:
{context}

Question:
{query}

Instructions:
1. Provide a concise, direct, and *useful* answer.
2. When asked about what files a user has (inventory/listing questions):
   - Lead with a high-level summary: total counts, file types, and project locations.
   - Group files by project, folder, or purpose — NOT by cryptic subfolder names.
   - Do NOT exhaustively list files that have auto-generated or hash-like names (e.g. Unreal .uasset files). Instead, state the count and location.
   - Focus on what the files *are* (e.g. "Unreal Engine external actor assets") rather than each individual filename.
3. When the context contains a "File Statistics" section, use those aggregate numbers as the primary data source for inventory questions.
4. When the context contains an "Indexed Project/Folder Profiles" section, use it to describe projects at a high level — mention the project type, what it contains, its key files and folder structure. This is your primary source for project-level questions.
5. Cite source files by their paths using [source_index] notation when relevant.
6. Be professional, helpful, and conversational — not robotic.

Answer:
"""

    async def generate_answer(self, query: str, context: str) -> str:
        """Generates an answer using the provided context and query."""
        prompt = self._build_prompt(query, context)
        if self.api_key:
            return await self._call_gemini(prompt)
        else:
            return await self._call_ollama(prompt)

    async def stream_answer(self, query: str, context: str) -> AsyncGenerator[str, None]:
        """Generates a streaming answer using the provided context and query."""
        prompt = self._build_prompt(query, context)
        if self.api_key:
            async for chunk in self._stream_gemini(prompt):
                yield chunk
        else:
            async for chunk in self._stream_ollama(prompt):
                yield chunk

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _call_gemini(self, prompt: str) -> str:
        """Calls the Google Gemini API with retry logic for transient failures."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        client = self._get_gemini_client()
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            logger.error("Gemini API error (%d): <redacted>", response.status_code)
            return f"Gemini API returned error {response.status_code}. Please check your API key or connection."
        
        data = response.json()
        try:
            if 'candidates' in data and data['candidates']:
                return data['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError, TypeError):
            pass
        logger.error("Unexpected Gemini response structure (keys: %s)", list(data.keys()))
        return "Error parsing Gemini response."

    async def _stream_gemini(self, prompt: str) -> AsyncGenerator[str, None]:
        """Streams response from Gemini API using server-sent events-like parsing."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:streamGenerateContent"
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
        client = self._get_gemini_client()
        try:
            async with client.stream("POST", url, params=params, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    yield f"Error: Gemini API returned {response.status_code}"
                    return

                # Gemini streamGenerateContent returns a JSON array of objects.
                # Use an incremental JSON decoder for robust nested-object parsing.
                decoder = json.JSONDecoder()
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while True:
                        # Strip leading whitespace, commas, and array brackets
                        buffer = buffer.lstrip(", \r\n\t[]")
                        if not buffer:
                            break
                        try:
                            data, end_idx = decoder.raw_decode(buffer)
                        except json.JSONDecodeError:
                            # Not enough data for a complete JSON value yet
                            break

                        if isinstance(data, dict) and "candidates" in data and data["candidates"]:
                            try:
                                text = (
                                    data["candidates"][0]
                                    .get("content", {})
                                    .get("parts", [{}])[0]
                                    .get("text")
                                )
                                if text:
                                    yield text
                            except (KeyError, IndexError, TypeError):
                                pass

                        buffer = buffer[end_idx:]
        except Exception:
            logger.exception("Gemini streaming error")
            yield "Streaming error. Please retry."

    async def _call_ollama(self, prompt: str) -> str:
        """Fallback to local Ollama instance."""
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False
        }
        
        try:
            client = self._get_ollama_client()
            response = await client.post(self.ollama_url, json=payload)
            if response.status_code == 200:
                data = response.json()
                return data.get("response", "No response from Ollama.")
            else:
                return "Local LLM (Ollama) not responding. Please provide a GEMINI_API_KEY or start Ollama."
        except Exception as e:
            logger.warning("Ollama fallback failed: %s", e)
            return "No LLM available."

    @staticmethod
    def _parse_ollama_line(line: str):
        """Parse a single line from Ollama streaming response."""
        if not line:
            return None, False
        try:
            data = json.loads(line)
            return data.get("response"), data.get("done", False)
        except json.JSONDecodeError:
            return None, False

    async def _stream_ollama(self, prompt: str) -> AsyncGenerator[str, None]:
        """Streams response from local Ollama instance."""
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": True
        }
        
        try:
            client = self._get_ollama_client()
            async with client.stream("POST", self.ollama_url, json=payload) as response:
                if response.status_code != 200:
                    yield "Local LLM (Ollama) error."
                    return
                async for line in response.aiter_lines():
                    text, done = self._parse_ollama_line(line)
                    if text:
                        yield text
                    if done:
                        break
        except Exception as e:
            logger.warning("Ollama streaming failed: %s", e)
            yield "Ollama error."
