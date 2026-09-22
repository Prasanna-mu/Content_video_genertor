from providers.base import LLMProvider
from providers.ollama import OllamaProvider
from config.settings import settings


def get_llm_provider() -> LLMProvider:
    provider_name = settings.LLM_PROVIDER.lower()

    if provider_name == "ollama":
        return OllamaProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")