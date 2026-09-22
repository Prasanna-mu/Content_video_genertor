import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///database/agent.db")

    INPUT_PPT_DIR: Path = Path(os.getenv("INPUT_PPT_DIR", "input/ppt"))

    PROCESSING_DIR: Path = Path(os.getenv("PROCESSING_DIR", "processing"))
    DELIVERY_DIR: Path = Path(os.getenv("DELIVERY_DIR", "delivery"))

    QUALITY_THRESHOLD: int = int(os.getenv("QUALITY_THRESHOLD", "5"))
    MAX_CONTENT_RETRIES: int = int(os.getenv("MAX_CONTENT_RETRIES", "3"))

    DEFAULT_TTS_PROVIDER: str = os.getenv("DEFAULT_TTS_PROVIDER", "local")

    SPEAKING_RATE_WPM_MIN: int = 130
    SPEAKING_RATE_WPM_MAX: int = 150

    DURATION_CONFIG = {
        "normal": {"min_minutes": 0.5, "max_minutes": 1.0},
        "intermediate": {"min_minutes": 1.0, "max_minutes": 2.0},
        "long": {"min_minutes": 2.0, "max_minutes": 3.0},
        "extreme": {"min_minutes": 3.0, "max_minutes": 4.0},
    }

    QUALITY_LEVELS = {
        "1": "beginner",
        "2": "intermediate",
        "3": "technical",
    }

    LENGTH_LEVELS = {
        "1": "normal",
        "2": "intermediate",
        "3": "long",
        "4": "extreme",
    }

    def get_target_word_range(self, length_level: str) -> dict:
        config = self.DURATION_CONFIG.get(length_level, self.DURATION_CONFIG["normal"])
        min_words = int(config["min_minutes"] * self.SPEAKING_RATE_WPM_MIN)
        max_words = int(config["max_minutes"] * self.SPEAKING_RATE_WPM_MAX)
        return {"min": min_words, "max": max_words}


settings = Settings()