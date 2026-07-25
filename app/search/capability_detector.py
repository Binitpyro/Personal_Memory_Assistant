import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.search.llm_client import LLMClient

logger = logging.getLogger(__name__)


class CapabilityDetector:
    _instance = None
    _capability_cache: dict[str, bool] = {}  # noqa: RUF012

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def detect_capabilities(self, llm_client: "LLMClient") -> bool:
        """
        Runs a single-shot probe to determine if the selected model supports
        structured <claim> tags. Returns True if supported, False otherwise.
        """
        model_class = llm_client.get_model_class()
        # For cloud models, we can generally assume they are capable.
        # But we still run the probe to be sure, or we can just bypass it.
        # Let's run it for everything that is not explicitly disabled.

        if model_class == "3b_local":
            logger.info("CapabilityDetector: Model class is 3b_local, disabling claim tags.")
            return False

        cache_key = f"{llm_client.provider_preference}_{llm_client.model}_{llm_client.ollama_model}_{llm_client.lm_studio_model}"

        if cache_key in self._capability_cache:
            return bool(self._capability_cache[cache_key])

        probe_prompt = (
            "Given: 'Python was created in 1991 by Guido van Rossum.'\n"
            'Output: <claim sources="[1]">Python was created in 1991</claim> by <claim sources="[1]">Guido van Rossum</claim>.\n\n'
            "Your turn. Format the following sentence using the exact same claim tags:\n"
            "Given: 'The Earth orbits the Sun.'\n"
            "Output:"
        )

        try:
            logger.info("CapabilityDetector: Running <claim> tag probe...")
            # We call generate_answer. Using history=[] to ensure no contamination.
            response = await llm_client.generate_answer(
                probe_prompt, context="", history=[], skip_capability_check=True
            )

            # Simple heuristic check:
            if "<claim" in response and "sources=" in response and "</claim>" in response:
                logger.info("CapabilityDetector: Model passed the <claim> tag probe.")
                self._capability_cache[cache_key] = True
                return True
            else:
                logger.info("CapabilityDetector: Model failed the <claim> tag probe.")
                self._capability_cache[cache_key] = False
                return False
        except Exception as e:
            logger.warning("CapabilityDetector: Probe failed due to error: %s", e)
            self._capability_cache[cache_key] = False
            return False

    def report_failure(self, llm_client: "LLMClient"):
        """Called when the model fails to follow the <claim> tag instructions on a real query."""
        cache_key = f"{llm_client.provider_preference}_{llm_client.model}_{llm_client.ollama_model}_{llm_client.lm_studio_model}"
        logger.warning(
            "CapabilityDetector: Model failed to produce <claim> tags in real query. Disabling capabilities for session."
        )
        self._capability_cache[cache_key] = False

    def reset_cache(self):
        self._capability_cache.clear()


capability_detector = CapabilityDetector()
