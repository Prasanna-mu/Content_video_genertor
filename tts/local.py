import os
import pyttsx3
import wave
import tempfile
import threading
import shutil
from tts.base import TTSProvider, TTSResult
from utils.logging import get_logger


logger = get_logger("tts.local")


class LocalTTSProvider(TTSProvider):
    def __init__(self, rate: int = 150, volume: float = 1.0, voice_id: str = None):
        self.rate = rate
        self.volume = volume
        self.voice_id = voice_id

    @property
    def provider_name(self) -> str:
        return "local"

    def is_available(self) -> bool:
        try:
            engine = pyttsx3.init()
            voices = engine.getProperty('voices')
            engine.stop()
            return len(voices) > 0
        except Exception:
            return False

    def get_supported_formats(self) -> list[str]:
        return ["wav"]

    def synthesize(self, text: str, output_path: str) -> TTSResult:
        """Synthesize text to speech audio file with timeout protection."""
        try:
            # Log the text being processed (truncated for brevity)
            logger.debug(f"[{self.provider_name}] Synthesizing text (length={len(text)}): {text[:200]!r}")

            # Create a fresh engine instance each call to avoid state-related hangs
            engine = pyttsx3.init()
            engine.setProperty('rate', self.rate)
            engine.setProperty('volume', self.volume)
            if self.voice_id:
                engine.setProperty('voice', self.voice_id)

            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Generate audio to a temporary file first
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                temp_path = tmp.name

            # Define the generation function to run in a thread
            def generate():
                engine.save_to_file(text, temp_path)
                engine.runAndWait()

            # Run generation in a thread with timeout
            thread = threading.Thread(target=generate)
            thread.start()
            thread.join(timeout=30)  # 30 seconds timeout

            if thread.is_alive():
                # Timeout occurred – try to stop the engine
                logger.warning(f"[{self.provider_name}] TTS timeout after 30 seconds for slide text: {text[:100]!r}")
                try:
                    engine.stop()
                except Exception:
                    pass
                # Give the thread a moment to clean up after stop
                thread.join(timeout=5)
                if thread.is_alive():
                    logger.error(f"[{self.provider_name}] TTS thread did not terminate after stop")
                    return TTSResult(success=False, error="TTS timeout")
                # If thread ended, we continue to check output

            # Check if temporary audio file was created
            if not os.path.exists(temp_path):
                logger.error(f"[{self.provider_name}] Temporary audio file not created")
                return TTSResult(success=False, error="Audio file not generated")

            # Move temp file to final output path
            shutil.move(temp_path, output_path)

            # Get duration of generated audio
            duration = self._get_audio_duration(output_path)

            return TTSResult(
                success=True,
                file_path=output_path,
                duration_seconds=duration
            )
        except Exception as e:
            logger.error(f"[{self.provider_name}] TTS error: {e}")
            return TTSResult(
                success=False,
                error=str(e)
            )
        finally:
            # Ensure engine is stopped to release resources
            try:
                engine.stop()
            except Exception:
                pass

    def _get_audio_duration(self, file_path: str) -> float:
        try:
            with wave.open(file_path, 'rb') as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                return frames / float(rate)
        except Exception:
            return 0.0

    def get_available_voices(self) -> list[dict]:
        try:
            engine = pyttsx3.init()
            voices = engine.getProperty('voices')
            engine.stop()
            return [
                {
                    "id": v.id,
                    "name": v.name,
                    "languages": v.languages,
                    "gender": v.gender
                }
                for v in voices
            ]
        except Exception:
            return []