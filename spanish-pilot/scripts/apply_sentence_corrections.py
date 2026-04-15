"""Apply sentence verification issues by asking Claude to regenerate just the flagged fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/stian/src/limbic")
from limbic.cerebellum.claude_cli import generate

ROOT = Path(__file__).resolve().parent.parent
SENTENCES_PATH = ROOT / "content" / "sentences.json"
LEMMAS_PATH = ROOT / "content" / "lemmas.json"


def main() -> None:
    data = json.loads(SENTENCES_PATH.read_text())
    issues = data.get("verification_issues", [])
    if not issues:
        print("No issues to fix.")
        return

    lemmas_data = json.loads(LEMMAS_PATH.read_text())
    lemmas = lemmas_data.get("enriched_corrected", lemmas_data["enriched"])["lemmas"]
    allowed = [l["lemma_es"] for l in lemmas]

    schema = {
        "type": "object",
        "properties": {
            "sentences": data["sentences"].get if False else {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "es": {"type": "string"},
                        "no": {"type": "string"},
                        "word_mapping": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "position": {"type": "integer"},
                                    "form": {"type": "string"},
                                    "lemma_es": {"type": "string"},
                                    "grammatical_note": {"type": "string"},
                                },
                                "required": ["position", "form", "lemma_es", "grammatical_note"],
                            },
                        },
                        "distractors_no": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 3,
                            "maxItems": 3,
                        },
                        "difficulty_rank": {"type": "integer"},
                    },
                    "required": ["es", "no", "word_mapping", "distractors_no", "difficulty_rank"],
                },
            }
        },
        "required": ["sentences"],
    }

    issues_text = "\n".join(
        f"- s{iss['sentence_index']}.{iss['field']} [{iss['severity']}]: {iss['problem_no']}\n  Forslag: {iss['suggested_fix_no']}"
        for iss in issues
    )

    prompt = f"""Her er spanske øvingssetninger med flaggete problemer. Lag en korrigert versjon der ALLE flaggete problemer er fikset. Ikke endre noe som ikke er flagget. Behold antallet setninger og rekkefølgen.

Vokabularliste (ikke bruk ord utenfor denne): {allowed}

Flaggete problemer:
{issues_text}

Opprinnelige setninger:
```json
{json.dumps(data["sentences"], ensure_ascii=False, indent=2)}
```

Returner full korrigert JSON med alle {len(data['sentences']['sentences'])} setninger."""

    result, meta = generate(
        prompt=prompt,
        system="Du er en presis redaktør. Fiks kun det som er flagget, behold alt annet uendret.",
        schema=schema,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose="sentence_correction",
        max_budget=2.0,
        timeout=300,
    )

    data["sentences_corrected"] = result
    SENTENCES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Applied corrections for {len(issues)} issues")


if __name__ == "__main__":
    main()
