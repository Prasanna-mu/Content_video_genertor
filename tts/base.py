from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSResult:
    success: bool
    file_path: str = ""
    duration_seconds: float = 0.0
    error: str = ""


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, output_path: str) -> TTSResult:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def get_supported_formats(self) -> list[str]:
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass