import tempfile

import scipy.io.wavfile as wavfile
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pocket_tts import TTSModel
from pydantic import BaseModel

app = FastAPI(title="Pocket TTS Internal API")


print("Loading Pocket TTS...")
tts_model = TTSModel.load_model("english")

print("Loading voice...")
voice_state = tts_model.get_state_for_audio_prompt(
    "hf://kyutai/tts-voices/alba-mackenna/casual.wav"
)

print("Pocket TTS ready!")


class GenerateRequest(BaseModel):
    text: str


@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "pocket-tts",
        "version": "2.1.0",
    }


@app.post("/generate")
def generate(request: GenerateRequest):
    if not request.text.strip():
        return {"error": "Text cannot be empty"}

    audio = tts_model.generate_audio(
        voice_state,
        request.text
    )

    output_file = tempfile.NamedTemporaryFile(
        suffix=".wav",
        delete=False
    ).name

    wavfile.write(
        output_file,
        tts_model.sample_rate,
        audio.numpy()
    )

    return FileResponse(
        output_file,
        media_type="audio/wav",
        filename="pocket-tts.wav"
    )
    
if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )    