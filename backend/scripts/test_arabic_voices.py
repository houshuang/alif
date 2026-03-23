#!/usr/bin/env python3
"""Test ElevenLabs Arabic voices with sample phrases.

Generates audio samples for comparison. Saves to data/voice-samples/.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.services.tts import generate_audio, DEFAULT_VOICE_SETTINGS

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "voice-samples"

# Test phrases:
# 1. Emphatic consonants: ظ vs ض, ط vs ت, ص vs س
# 2. Normal story-like narrative
TEST_PHRASES = {
    "emphatics": "نَظَرَ الرَّجُلُ إِلَى الظَّلامِ وَضَرَبَ الطَّبْلَ بِقُوَّةٍ",
    "story": "ذَهَبَ أَحْمَدُ إِلَى السُّوقِ وَاشْتَرَى خُبْزًا وَحَلِيبًا. كَانَ الْجَوُّ جَمِيلًا وَالشَّمْسُ مُشْرِقَةً",
}

# Curated male voices to test — mix of known-good multilingual voices
# Sources: ElevenLabs voice library, community recommendations
VOICES_TO_TEST = [
    # Current default
    {"id": "G1HOkzin3NMwRHSq60UI", "name": "Chaouki (current)"},
    # Popular multilingual male voices
    {"id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel"},
    {"id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Liam"},
    {"id": "N2lVS1w4EtoT3dr4eOWO", "name": "Callum"},
    {"id": "bIHbv24MWmeRgasZH58o", "name": "Will"},
    {"id": "JBFqnCBsd6RMkjVDRZzb", "name": "George"},
    {"id": "cjVigY5qzO86Huf0OWal", "name": "Eric"},
    {"id": "iP95p4xoKVk53GoZ742B", "name": "Chris"},
    {"id": "pqHfZKP75CvOlQylNhV4", "name": "Bill"},
    {"id": "nPczCjzI2devNBz1zQrb", "name": "Brian"},
    {"id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte"},  # female for contrast
]


async def generate_samples():
    """Generate audio samples for each voice × phrase combination."""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for voice in VOICES_TO_TEST:
        vid = voice["id"]
        vname = voice["name"].replace(" ", "_").replace("(", "").replace(")", "")
        print(f"\n{voice['name']}:")

        for phrase_key, phrase_text in TEST_PHRASES.items():
            filename = f"{vname}_{phrase_key}.mp3"
            filepath = SAMPLE_DIR / filename

            if filepath.exists():
                print(f"  [cached] {filename}")
                results.append({"voice": voice["name"], "id": vid, "phrase": phrase_key, "file": filename})
                continue

            try:
                settings = dict(DEFAULT_VOICE_SETTINGS)
                settings["speed"] = 0.75

                audio = await generate_audio(
                    text=phrase_text,
                    voice_id=vid,
                    voice_settings=settings,
                )
                filepath.write_bytes(audio)
                size_kb = len(audio) / 1024
                print(f"  [ok] {filename} ({size_kb:.0f} KB)")
                results.append({"voice": voice["name"], "id": vid, "phrase": phrase_key, "file": filename})
            except Exception as e:
                print(f"  [FAIL] {filename}: {e}")
                results.append({"voice": voice["name"], "id": vid, "phrase": phrase_key, "file": None, "error": str(e)})

    return results


async def main():
    print("=== ElevenLabs Arabic Voice Test ===")
    print(f"Testing {len(VOICES_TO_TEST)} voices × {len(TEST_PHRASES)} phrases\n")

    results = await generate_samples()

    # Save manifest
    manifest = {"phrases": TEST_PHRASES, "voices": VOICES_TO_TEST, "results": results}
    (SAMPLE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    ok = [r for r in results if r.get("file")]
    fail = [r for r in results if not r.get("file")]
    print(f"\n{'─' * 40}")
    print(f"Generated: {len(ok)} samples, Failed: {len(fail)}")
    print(f"Samples at: {SAMPLE_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
