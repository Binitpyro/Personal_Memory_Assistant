from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.registry import spec_of


class OpenAICompatibleProviderInstance(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        default_model: str | None,
        timeout: float = 30.0
    ):
        spec = spec_of("openai_compatible")
        super().__init__(
            spec,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            timeout=timeout,
        )
