import logging
import httpx
import json
from typing import Optional, AsyncGenerator, List, Dict
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import settings

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        self.api_key = settings.gemini_api_key
        self.ollama_url = settings.ollama_url
        self.ollama_model = settings.ollama_model
        self.model = settings.gemini_model
        self.provider_preference = "auto"
        self.lm_studio_url = "http://localhost:1234/v1/chat/completions"
        self.lm_studio_model = ""
        self._gemini_client: Optional[httpx.AsyncClient] = None
        self._ollama_client: Optional[httpx.AsyncClient] = None
        self._lm_studio_client: Optional[httpx.AsyncClient] = None
        self._oauth_token = self._load_oauth_token()
        self._load_runtime_preferences()

    def _load_oauth_token(self) -> Optional[str]:
        token_path = Path("data/credentials.json")
        if token_path.exists():
            try:
                import json
                from google.oauth2.credentials import Credentials
                from google.auth.transport.requests import Request as GoogleRequest
                with open(token_path, "r") as f:
                    token_data = json.load(f)
                creds = Credentials.from_authorized_user_info(token_data)
                if not creds.valid:
                    if creds.expired and creds.refresh_token:
                        creds.refresh(GoogleRequest())
                        token_data['token'] = creds.token
                        with open(token_path, "w") as f:
                            json.dump(token_data, f)
                if creds.valid:
                    return creds.token
            except Exception as e:
                logger.warning("Failed to load OAuth token: %s", e)
        return None

    def _get_gemini_client(self) -> httpx.AsyncClient:
        if self._gemini_client is None or self._gemini_client.is_closed:
            self._gemini_client = httpx.AsyncClient(timeout=settings.gemini_timeout)
        return self._gemini_client

    def _get_ollama_client(self) -> httpx.AsyncClient:
        if self._ollama_client is None or self._ollama_client.is_closed:
            self._ollama_client = httpx.AsyncClient(timeout=settings.ollama_timeout)
        return self._ollama_client

    def _get_lm_studio_client(self) -> httpx.AsyncClient:
        if self._lm_studio_client is None or self._lm_studio_client.is_closed:
            self._lm_studio_client = httpx.AsyncClient(timeout=settings.ollama_timeout)
        return self._lm_studio_client

    def _load_runtime_preferences(self) -> None:
        pref_path = Path("data/settings.json")
        if not pref_path.exists():
            return
        try:
            with open(pref_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            llm_prefs = data.get("llm", {})
            self.provider_preference = llm_prefs.get("provider", "auto")
            self.model = llm_prefs.get("gemini_model", self.model)
            self.ollama_model = llm_prefs.get("ollama_model", self.ollama_model)
            self.lm_studio_model = llm_prefs.get("lm_studio_model", self.lm_studio_model)
        except Exception as e:
            logger.debug("Unable to load runtime LLM preferences: %s", e)

    def apply_preferences(
        self,
        provider: Optional[str] = None,
        gemini_model: Optional[str] = None,
        ollama_model: Optional[str] = None,
        lm_studio_model: Optional[str] = None,
    ) -> None:
        if provider:
            self.provider_preference = provider
        if gemini_model:
            self.model = gemini_model
        if ollama_model:
            self.ollama_model = ollama_model
        if lm_studio_model is not None:
            self.lm_studio_model = lm_studio_model

    def _build_prompt(self, query: str, context: str) -> str:
        return f"""
You are a personal memory assistant. Answer the user's question using ONLY the provided context snippets.
If the answer is not in the context, say "I don't have enough information in your indexed files to answer this."

Instructions:
1. Provide a detailed, comprehensive, and well-structured response for complex problems or technical queries.
2. For simple or factual questions, you may be concise and direct.
3. If a 'Metadata Insights' block is provided in the context, treat its data as the absolute source of truth for file counts, sizes, and dates.
4. Group information logically (e.g., by project, folder, or purpose).
5. Cite source files by their paths using [source_index] notation.
6. Do not use excessive filler words. Be professional, direct, and thorough where needed.

### Context
{context}

### Question
{query}

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
        provider = (self.provider_preference or "auto").lower()

        if provider in {"gemini", "auto"} and (self.api_key or self._oauth_token):
            return await self._call_gemini(prompt, history=history)
        if provider in {"lm_studio", "auto"} and await self._check_lm_studio_health():
            return await self._call_lm_studio(prompt, history=history)
        if provider in {"ollama", "auto"} and await self._check_ollama_health():
            return await self._call_ollama(prompt)
        return "LLM unavailable. Please provide a GEMINI_API_KEY or ensure Ollama is running."

    async def stream_answer(self, query: str, context: str, history: Optional[List[Dict[str, str]]] = None) -> AsyncGenerator[str, None]:
        prompt = self._build_prompt(query, context)
        provider = (self.provider_preference or "auto").lower()

        if provider in {"gemini", "auto"} and (self.api_key or self._oauth_token):
            async for chunk in self._stream_gemini(prompt, history=history):
                yield chunk
            return
        if provider in {"lm_studio", "auto"} and await self._check_lm_studio_health():
            async for chunk in self._stream_lm_studio(prompt, history=history):
                yield chunk
            return
        if provider in {"ollama", "auto"} and await self._check_ollama_health():
            async for chunk in self._stream_ollama(prompt):
                yield chunk
            return
        yield "LLM unavailable."

    async def _check_lm_studio_health(self) -> bool:
        try:
            client = self._get_lm_studio_client()
            resp = await client.get(self.lm_studio_url.replace("/chat/completions", "/models"), timeout=1.0)
            return resp.status_code == 200
        except Exception:
            return False

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
                "maxOutputTokens": settings.gemini_max_output_tokens,
            },
        }
        
        client = self._get_gemini_client()
        try:
            # If using OAuth
            if self._oauth_token:
                headers = {"Authorization": f"Bearer {self._oauth_token}"}
                response = await client.post(url, headers=headers, json=payload)
            else:
                # Try with ?key= parameter first
                response = await client.post(url, params={"key": self.api_key}, json=payload)
                
                if response.status_code != 200:
                    logger.error("Gemini error %d: %s", response.status_code, response.text)
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
                "maxOutputTokens": settings.gemini_max_output_tokens,
            },
        }
        client = self._get_gemini_client()
        try:
            if self._oauth_token:
                headers = {"Authorization": f"Bearer {self._oauth_token}"}
                req = client.stream("POST", url, headers=headers, json=payload)
            else:
                req = client.stream("POST", url, params={"key": self.api_key}, json=payload)
                
            async with req as response:
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

    async def _call_lm_studio(self, prompt: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        try:
            client = self._get_lm_studio_client()
            messages = history[:] if history else []
            messages.append({"role": "user", "content": prompt})
            model_name = self.lm_studio_model or "local-model"
            resp = await client.post(
                self.lm_studio_url,
                json={"model": model_name, "messages": messages, "stream": False, "temperature": 0.2},
            )
            if resp.status_code != 200:
                return f"LM Studio error {resp.status_code}"
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "No response.")
        except Exception:
            return "LM Studio failed."

    async def _stream_lm_studio(self, prompt: str, history: Optional[List[Dict[str, str]]] = None) -> AsyncGenerator[str, None]:
        try:
            client = self._get_lm_studio_client()
            messages = history[:] if history else []
            messages.append({"role": "user", "content": prompt})
            model_name = self.lm_studio_model or "local-model"
            async with client.stream(
                "POST",
                self.lm_studio_url,
                json={"model": model_name, "messages": messages, "stream": True, "temperature": 0.2},
            ) as resp:
                if resp.status_code != 200:
                    yield f"LM Studio error {resp.status_code}"
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        parsed = json.loads(payload)
                        delta = parsed.get("choices", [{}])[0].get("delta", {}).get("content")
                        if delta:
                            yield delta
                    except Exception:
                        continue
        except Exception:
            yield "LM Studio stream failed."
