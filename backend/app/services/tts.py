import hashlib
import json
import os
from pathlib import Path
from typing import Optional

import httpx

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_turbo_v2_5"
DEFAULT_VOICE_ID = "G1HOkzin3NMwRHSq60UI"  # Chaouki — MSA male, clear neutral accent
DEFAULT_VOICE_SETTINGS = {
    "stability": 0.85,
    "similarity_boost": 0.75,
    "style": 0.0,
    "speed": 0.8,
    "use_speaker_boost": True,
}

AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "audio"
INDEX_FILE = AUDIO_DIR / "index.json"


class TTSError(Exception):
    pass


class TTSKeyMissing(TTSError):
    pass


def _get_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise TTSKeyMissing("ELEVENLABS_API_KEY environment variable is not set")
    return key


def _ensure_audio_dir() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def cache_key_for(text: str, voice_id: str) -> str:
    h = hashlib.sha256(f"{text}|{voice_id}".encode()).hexdigest()
    return h


def get_cached_path(cache_key: str) -> Optional[Path]:
    path = AUDIO_DIR / f"{cache_key}.mp3"
    if path.exists():
        return path
    return None


def _load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text())
    return {}


def _save_index(index: dict) -> None:
    _ensure_audio_dir()
    INDEX_FILE.write_text(json.dumps(index, indent=2))


async def list_voices(api_key: Optional[str] = None) -> list[dict]:
    key = api_key or _get_api_key()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ELEVENLABS_BASE_URL}/voices",
            headers={"xi-api-key": key},
            timeout=30.0,
        )
    if resp.status_code != 200:
        raise TTSError(f"Failed to list voices: {resp.status_code} - {resp.text}")
    return resp.json()["voices"]


def filter_arabic_compatible_voices(voices: list[dict]) -> list[dict]:
    results = []
    for v in voices:
        if v.get("category") == "premade":
            results.append(v)
            continue
        labels = v.get("labels", {})
        if labels.get("language") == "multilingual":
            results.append(v)
            continue
        accent = (labels.get("accent") or "").lower()
        if "arabic" in accent or "middle eastern" in accent:
            results.append(v)
    return results


async def generate_audio(
    text: str,
    voice_id: str,
    api_key: Optional[str] = None,
    model_id: str = DEFAULT_MODEL,
    voice_settings: Optional[dict] = None,
) -> bytes:
    key = api_key or _get_api_key()
    settings = voice_settings or DEFAULT_VOICE_SETTINGS

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": model_id,
                "language_code": "ar",
                "apply_text_normalization": "on",
                "voice_settings": settings,
            },
            timeout=60.0,
        )

    if resp.status_code == 429:
        raise TTSError("ElevenLabs rate limit exceeded. Try again later.")
    if resp.status_code != 200:
        raise TTSError(f"Failed to generate audio: {resp.status_code} - {resp.text}")

    return resp.content


async def generate_and_cache(
    text: str,
    voice_id: str,
    cache_key: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Path:
    _ensure_audio_dir()
    key = cache_key or cache_key_for(text, voice_id)
    cached = get_cached_path(key)
    if cached:
        return cached

    audio_bytes = await generate_audio(text, voice_id, api_key=api_key)
    path = AUDIO_DIR / f"{key}.mp3"
    path.write_bytes(audio_bytes)
    return path


def update_index(sentence_id: int | str, filename: str) -> None:
    index = _load_index()
    index[str(sentence_id)] = filename
    _save_index(index)


def get_audio_total_size() -> int:
    _ensure_audio_dir()
    return sum(f.stat().st_size for f in AUDIO_DIR.iterdir() if f.suffix == ".mp3")
