from tts.base import TTSProvider
from tts.local import LocalTTSProvider
from config.settings import settings


def get_tts_provider() -> TTSProvider:
    provider_name = settings.DEFAULT_TTS_PROVIDER.lower()

    if provider_name == "local":
        return LocalTTSProvider()
    else:
        raise ValueError(f"Unknown TTS provider: {provider_name}")