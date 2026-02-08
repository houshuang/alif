from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services.tts import (
    AUDIO_DIR,
    TTSError,
    TTSKeyMissing,
    cache_key_for,
    filter_arabic_compatible_voices,
    generate_and_cache,
    list_voices,
    update_index,
)

router = APIRouter(prefix="/api/tts", tags=["tts"])


class GenerateRequest(BaseModel):
    text: str
    voice_id: str | None = None


class GenerateForSentenceRequest(BaseModel):
    sentence_id: int
    text: str
    voice_id: str | None = None


DEFAULT_VOICE_ID = "G1HOkzin3NMwRHSq60UI"  # "Chaouki" — MSA male, clear neutral accent


@router.get("/voices")
async def get_voices():
    try:
        voices = await list_voices()
    except TTSKeyMissing:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs API key not configured. Set ELEVENLABS_API_KEY.",
        )
    except TTSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    arabic_voices = filter_arabic_compatible_voices(voices)
    return {"voices": arabic_voices}


@router.post("/generate")
async def generate(req: GenerateRequest):
    voice_id = req.voice_id or DEFAULT_VOICE_ID
    try:
        path = await generate_and_cache(req.text, voice_id)
    except TTSKeyMissing:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs API key not configured. Set ELEVENLABS_API_KEY.",
        )
    except TTSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@router.post("/generate-for-sentence")
async def generate_for_sentence(req: GenerateForSentenceRequest):
    voice_id = req.voice_id or DEFAULT_VOICE_ID
    cache_key = cache_key_for(req.text, voice_id)
    try:
        path = await generate_and_cache(req.text, voice_id, cache_key=cache_key)
    except TTSKeyMissing:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs API key not configured. Set ELEVENLABS_API_KEY.",
        )
    except TTSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    update_index(req.sentence_id, path.name)
    return {
        "sentence_id": req.sentence_id,
        "audio_url": f"/api/tts/audio/{path.name}",
        "cached": True,
    }


@router.get("/audio/{filename}")
async def serve_audio(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(path, media_type="audio/mpeg")
