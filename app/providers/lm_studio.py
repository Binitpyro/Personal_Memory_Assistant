from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.registry import spec_of


class LMStudioProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float = 30.0
    ):
        spec = spec_of("lm_studio")
        super().__init__(
            spec,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            timeout=timeout,
        )
