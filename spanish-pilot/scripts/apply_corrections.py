"""Apply verification-found issues to the enriched lemmas via a single correction call.

Reads content/lemmas.json (which has both enriched + verification_issues),
asks Claude to produce a corrected version, writes back.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/stian/src/limbic")
from limbic.cerebellum.claude_cli import generate

ROOT = Path(__file__).resolve().parent.parent
LEMMAS_PATH = ROOT / "content" / "lemmas.json"


def apply_corrections(enriched: dict, issues: list[dict]) -> dict:
    if not issues:
        return enriched

    # Build schema that matches the enriched structure
    item_schema = {
        "type": "object",
        "properties": {
            "lemma_es": {"type": "string"},
            "pos": {"type": "string"},
            "gloss_no": {"type": "string"},
            "gender": {"type": "string"},
            "article_quirk": {"type": "string"},
            "conjugation_applicable": {"type": "boolean"},
            "conjugation_present": {
                "type": "object",
                "properties": {
                    "yo": {"type": "string"},
                    "tu": {"type": "string"},
                    "el": {"type": "string"},
                    "nosotros": {"type": "string"},
                    "vosotros": {"type": "string"},
                    "ellos": {"type": "string"},
                },
                "required": ["yo", "tu", "el", "nosotros", "vosotros", "ellos"],
            },
            "agreement_forms": {
                "type": "object",
                "properties": {
                    "masc_sg": {"type": "string"},
                    "fem_sg": {"type": "string"},
                    "masc_pl": {"type": "string"},
                    "fem_pl": {"type": "string"},
                },
            },
            "plural_form": {"type": "string"},
            "memory_hook_no": {"type": "string"},
            "etymology_no": {"type": "string"},
            "example_es": {"type": "string"},
            "example_no": {"type": "string"},
            "cefr_level": {"type": "string"},
            "frequency_rank_estimate": {"type": "integer"},
        },
        "required": [
            "lemma_es", "pos", "gloss_no", "gender", "article_quirk",
            "conjugation_applicable", "memory_hook_no", "etymology_no",
            "example_es", "example_no", "cefr_level", "frequency_rank_estimate",
        ],
    }
    schema = {
        "type": "object",
        "properties": {
            "lemmas": {"type": "array", "items": item_schema}
        },
        "required": ["lemmas"],
    }

    # Filter out schema-artifact issues (keys like conjugation_present.tu being labels, not content)
    real_issues = [
        iss for iss in issues
        if not (iss["field"].startswith("conjugation_present.")
                and "aksent" in iss["problem_no"].lower()
                and iss["severity"] == "critical")
    ]
    if not real_issues:
        return enriched

    issues_text = "\n".join(
        f"- {iss['lemma_es']}.{iss['field']} [{iss['severity']}]: {iss['problem_no']}\n  Forslag: {iss['suggested_fix_no']}"
        for iss in real_issues
    )

    prompt = f"""Her er en JSON med spansk ordinformasjon for norske elever, og en liste med problemer funnet under korrekturlesing. Lag en oppdatert versjon der ALLE reelle problemer er fikset. Ikke endre noe som ikke er flagget.

Problemer å fikse:
{issues_text}

Opprinnelig JSON:
```json
{json.dumps(enriched, ensure_ascii=False, indent=2)}
```

Returner full korrigert JSON. Behold alle ord (12 totalt), fiks kun de flaggete feltene."""

    result, meta = generate(
        prompt=prompt,
        system="Du er en presis redaktør for et spansk læremiddel. Fiks kun det som er flagget, behold alt annet uendret.",
        schema=schema,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose="lemma_correction",
        max_budget=2.0,
        timeout=300,
    )
    return result


def main() -> None:
    data = json.loads(LEMMAS_PATH.read_text())
    enriched = data["enriched"]
    issues = data.get("verification_issues", [])
    print(f"Applying corrections for {len(issues)} issues...")

    corrected = apply_corrections(enriched, issues)
    print(f"  corrected version has {len(corrected.get('lemmas', []))} lemmas")

    data["enriched_corrected"] = corrected
    LEMMAS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Wrote corrected version to {LEMMAS_PATH}")


if __name__ == "__main__":
    main()
