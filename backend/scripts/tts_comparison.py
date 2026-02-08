#!/usr/bin/env python3
"""Generate TTS comparison samples across models, voices, and settings."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Map local env var name
if not os.environ.get("ELEVENLABS_API_KEY"):
    os.environ["ELEVENLABS_API_KEY"] = os.environ.get("ELEVENLABS_KEY", "")

from app.services.tts import generate_audio

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "tts_comparison"

# Two test sentences: one simple, one complex
SENTENCES = {
    "simple": "ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ",       # The boy went to school
    "complex": "كَانَتِ الشَّمْسُ تَغْرُبُ خَلْفَ الجِبَالِ البَعِيدَةِ",  # The sun was setting behind the distant mountains
}

# Models to compare
MODELS = {
    "turbo_v2.5": "eleven_turbo_v2_5",
    "multilingual_v2": "eleven_multilingual_v2",
    "v3": "eleven_v3",
}

# Voices to compare (known IDs from ElevenLabs voice library)
VOICES = {
    "Chaouki_MSA_male": "G1HOkzin3NMwRHSq60UI",
    "Raed_Saudi_male": "IK7YYZcSpmlkjKrQxbSn",
    "Fares_Gulf_male": "5Spsi3mCH9e7futpnGE5",
    "Salma_Levantine_female": "a1KZUXKFVFDOb33I1uqr",
}

# Settings variations
SETTINGS = {
    "default": {
        "stability": 0.85,
        "similarity_boost": 0.75,
        "style": 0.0,
        "speed": 0.8,
        "use_speaker_boost": True,
    },
    "slower": {
        "stability": 0.85,
        "similarity_boost": 0.75,
        "style": 0.0,
        "speed": 0.75,
        "use_speaker_boost": True,
    },
    "natural_speed": {
        "stability": 0.85,
        "similarity_boost": 0.75,
        "style": 0.0,
        "speed": 1.0,
        "use_speaker_boost": True,
    },
}




async def generate_sample(text, voice_id, model_id, settings, filename):
    """Generate a single sample and save it."""
    filepath = OUTPUT_DIR / filename
    if filepath.exists():
        print(f"  [cached] {filename}")
        return True

    # v3 doesn't support speaker_boost
    s = dict(settings)
    if model_id == "eleven_v3":
        s.pop("use_speaker_boost", None)

    try:
        audio = await generate_audio(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            voice_settings=s,
        )
        filepath.write_bytes(audio)
        print(f"  [ok] {filename} ({len(audio)} bytes)")
        return True
    except Exception as e:
        print(f"  [FAIL] {filename}: {e}")
        return False


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Generating comparison matrix ===")
    print(f"Sentences: {len(SENTENCES)}")
    print(f"Models: {list(MODELS.keys())}")
    print(f"Voices: {list(VOICES.keys())}")
    print(f"Settings: {list(SETTINGS.keys())}")

    # Phase 1: Model comparison (same voice=Chaouki, default settings, all models)
    print("\n--- Phase 1: Model comparison (Chaouki, default settings) ---")
    for sent_key, text in SENTENCES.items():
        for model_key, model_id in MODELS.items():
            fname = f"1_model_{sent_key}_{model_key}_chaouki.mp3"
            await generate_sample(text, VOICES["Chaouki_MSA_male"], model_id, SETTINGS["default"], fname)

    # Phase 2: Voice comparison (same model=multilingual_v2, default settings, all voices)
    print("\n--- Phase 2: Voice comparison (multilingual_v2, default settings) ---")
    for sent_key, text in SENTENCES.items():
        for voice_key, voice_id in VOICES.items():
            fname = f"2_voice_{sent_key}_{voice_key}_mlv2.mp3"
            await generate_sample(text, voice_id, "eleven_multilingual_v2", SETTINGS["default"], fname)

    # Phase 3: Speed comparison (Chaouki, multilingual_v2, all speeds)
    print("\n--- Phase 3: Speed comparison (Chaouki, multilingual_v2) ---")
    for sent_key, text in SENTENCES.items():
        for sett_key, sett in SETTINGS.items():
            fname = f"3_speed_{sent_key}_{sett_key}_chaouki_mlv2.mp3"
            await generate_sample(text, VOICES["Chaouki_MSA_male"], "eleven_multilingual_v2", sett, fname)

    # Summary
    files = sorted(OUTPUT_DIR.glob("*.mp3"))
    total_bytes = sum(f.stat().st_size for f in files)
    print(f"\n=== Done! {len(files)} samples in {OUTPUT_DIR} ({total_bytes/1024:.0f} KB) ===")
    print("\nFiles organized as:")
    print("  1_model_*  — Compare models (turbo_v2.5 vs multilingual_v2 vs v3)")
    print("  2_voice_*  — Compare voices (all Arabic voices, multilingual_v2)")
    print("  3_speed_*  — Compare speeds (0.75 vs 0.8 vs 1.0)")
    print(f"\nOpen folder: open {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
