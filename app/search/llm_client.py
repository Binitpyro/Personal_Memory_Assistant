import logging
import httpx
import json
from typing import Optional, AsyncGenerator, List, Dict
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
        if self._gemini_client is None or self._gemini_client.is_closed:
            self._gemini_client = httpx.AsyncClient(timeout=settings.gemini_timeout)
        return self._gemini_client

    def _get_ollama_client(self) -> httpx.AsyncClient:
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
2. Group files by project, folder, or purpose.
3. Cite source files by their paths using [source_index] notation.
4. Be professional and conversational.

Answer:
"""

    async def _check_ollama_health(self) -> bool:
        try:
            client = self._get_ollama_client()
            resp = await client.get(self.ollama_url.replace("/api/generate", "/api/tags"), timeout=1.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def generate_answer(self, query: str, context: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        prompt = self._build_prompt(query, context)
        if self.api_key:
            return await self._call_gemini(prompt, history=history)
        if await self._check_ollama_health():
            return await self._call_ollama(prompt)
        return "LLM unavailable. Please provide a GEMINI_API_KEY or ensure Ollama is running."

    async def stream_answer(self, query: str, context: str, history: Optional[List[Dict[str, str]]] = None) -> AsyncGenerator[str, None]:
        prompt = self._build_prompt(query, context)
        if self.api_key:
            async for chunk in self._stream_gemini(prompt, history=history):
                yield chunk
            return
        if await self._check_ollama_health():
            async for chunk in self._stream_ollama(prompt):
                yield chunk
            return
        yield "LLM unavailable."

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _call_gemini(self, prompt: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        # Production v1 endpoint
        url = f"https://generativelanguage.googleapis.com/v1/models/{self.model}:generateContent"
        
        # Diagnostics
        key_preview = self.api_key[:6] + "..." if len(self.api_key) > 6 else "****"
        logger.info("Gemini Request: %s (model: %s, key: %s)", url, self.model, key_preview)
        
        # Build contents array with history
        contents = []
        if history:
            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        
        # Add current prompt as latest user message
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
                "maxOutputTokens": 1024,
            },
        }
        
        client = self._get_gemini_client()
        try:
            # Try with ?key= parameter first, as it's the most common for AI Studio keys
            response = await client.post(url, params={"key": self.api_key}, json=payload)
            
            if response.status_code != 200:
                logger.error("Gemini error %d: %s", response.status_code, response.text)
                # Fallback: try with x-goog-api-key header if param failed with 404/401
                if response.status_code in (404, 401):
                    logger.info("Retrying Gemini with header-based auth...")
                    response = await client.post(url, headers={"x-goog-api-key": self.api_key}, json=payload)
            
            if response.status_code != 200:
                return f"Gemini API error {response.status_code}: {response.text[:100]}"
            
            data = response.json()
            return data['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            logger.error("Gemini request failed: %s", str(e))
            raise

    async def _stream_gemini(self, prompt: str, history: Optional[List[Dict[str, str]]] = None) -> AsyncGenerator[str, None]:
        url = f"https://generativelanguage.googleapis.com/v1/models/{self.model}:streamGenerateContent"
        
        contents = []
        if history:
            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
                "maxOutputTokens": 1024,
            },
        }
        client = self._get_gemini_client()
        try:
            async with client.stream("POST", url, params={"key": self.api_key}, json=payload) as response:
                if response.status_code != 200:
                    yield f"Error: {response.status_code}"
                    return
                decoder = json.JSONDecoder()
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    buffer, new_texts = self._parse_stream_buffer(decoder, buffer)
                    for text in new_texts:
                        yield text
        except Exception as e:
            logger.error("Gemini stream failed: %s", e)
            yield "Streaming error."

    def _parse_stream_buffer(self, decoder: json.JSONDecoder, buffer: str) -> tuple[str, list[str]]:
        new_texts = []
        while True:
            buffer = buffer.lstrip(", \r\n\t[]")
            if not buffer: break
            try:
                data, end_idx = decoder.raw_decode(buffer)
                if isinstance(data, dict) and "candidates" in data:
                    text = data["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text")
                    if text: new_texts.append(text)
                buffer = buffer[end_idx:]
            except json.JSONDecodeError: break
        return buffer, new_texts

    async def _call_ollama(self, prompt: str) -> str:
        try:
            client = self._get_ollama_client()
            resp = await client.post(self.ollama_url, json={"model": self.ollama_model, "prompt": prompt, "stream": False})
            return resp.json().get("response", "No response.") if resp.status_code == 200 else "Ollama error."
        except Exception: return "Ollama failed."

    async def _stream_ollama(self, prompt: str) -> AsyncGenerator[str, None]:
        try:
            client = self._get_ollama_client()
            async with client.stream("POST", self.ollama_url, json={"model": self.ollama_model, "prompt": prompt, "stream": True}) as resp:
                async for line in resp.aiter_lines():
                    if not line: continue
                    chunk = json.loads(line).get("response")
                    if chunk: yield chunk
        except Exception: yield "Ollama stream failed."
