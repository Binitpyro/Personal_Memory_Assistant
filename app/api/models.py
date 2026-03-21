import httpx
import logging
from fastapi import APIRouter
from typing import Dict, Any

logger = logging.getLogger(__name__)

models_router = APIRouter(prefix="/llm", tags=["llm"])

@models_router.get("/detect")
async def detect_local_models() -> Dict[str, Any]:
    """
    Rapidly probes standard local ports to detect active LLM providers.
    Returns the list of available models for each provider.
    """
    results = {
        "ollama": {"detected": False, "models": []},
        "lm_studio": {"detected": False, "models": []}
    }
    
    # Use a very short timeout so the UI doesn't hang if they are offline
    async with httpx.AsyncClient(timeout=1.5) as client:
        # Probe Ollama
        try:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name") for m in data.get("models", [])]
                results["ollama"] = {"detected": True, "models": models}
        except Exception as e:
            logger.debug("Ollama detection skipped: %s", e)
            
        # Probe LM Studio (OpenAI compatible endpoint)
        try:
            resp = await client.get("http://localhost:1234/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])]
                results["lm_studio"] = {"detected": True, "models": models}
        except Exception as e:
            logger.debug("LM Studio detection skipped: %s", e)

    return results
