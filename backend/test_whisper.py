import whisper
from pathlib import Path

audio_path = Path(__file__).parent / "test.wav"

model = whisper.load_model("base")
result = model.transcribe(str(audio_path))
print(result["text"])
