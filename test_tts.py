import sys
sys.path.insert(0, '.')

from tts.local import LocalTTSProvider
import tempfile
import os

provider = LocalTTSProvider()
print("Provider available:", provider.is_available())

with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
    output_path = f.name

try:
    result = provider.synthesize("This is a test sentence.", output_path)
    print("Result:", result)
    if result.success:
        print("Audio file created:", output_path, "size:", os.path.getsize(output_path))
    else:
        print("Error:", result.error)
finally:
    if os.path.exists(output_path):
        os.remove(output_path)