"""Enrich seed lemmas via Claude CLI with JSON-schema constrained output.

Processes in batches of 20 to keep response sizes sane. Writes merged output
to content/lemmas.json.

Each batch: generate → verify → self-correct in one script, then merged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/stian/src/limbic")
from limbic.cerebellum.claude_cli import generate

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "content" / "seed_lemmas.json"
OUT_PATH = ROOT / "content" / "lemmas.json"

BATCH_SIZE = 20

LEMMA_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "lemma_es": {"type": "string"},
        "pos": {"type": "string", "enum": ["verb", "noun", "adjective", "adverb", "pronoun", "article", "preposition", "conjunction", "interjection", "numeral"]},
        "gloss_no": {"type": "string"},
        "gender": {"type": "string", "enum": ["m", "f", "mf", "none"]},
        "article_quirk": {"type": "string"},
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
        "conjugation_applicable": {"type": "boolean"},
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
        "cefr_level": {"type": "string", "enum": ["A1", "A2", "B1", "B2"]},
        "frequency_rank_estimate": {"type": "integer"},
    },
    "required": [
        "lemma_es", "pos", "gloss_no", "gender", "article_quirk",
        "conjugation_applicable", "memory_hook_no", "etymology_no",
        "example_es", "example_no", "cefr_level", "frequency_rank_estimate",
    ],
}

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {"lemmas": {"type": "array", "items": LEMMA_ITEM_SCHEMA}},
    "required": ["lemmas"],
}


SYSTEM = """Du er ekspert på spansk lingvistikk og norsk-spansk kontrastiv grammatikk. Du skriver alt på norsk bokmål for norske elever som lærer spansk.

HARDE REGLER:
- Alt innhold på norsk bokmål — INGEN engelsk tekst.
- Oversettelser skal være NATURLIGE — ikke ord-for-ord.
- Memory hooks må være GENUINT NYTTIGE (koble til lyd, bilde, kognat, eller karakteristisk særegenhet) — ikke banale.
- Etymologi må være FAKTISK — bare nevn latinske røtter og kognater du er sikker på.
- Konjugasjon kun presens indikativ. Alle 6 former må fylles ut for verb.
- For substantiv: fyll ut plural_form.
- For adjektiv/artikkel: fyll ut agreement_forms (masc_sg, fem_sg, masc_pl, fem_pl).
- For invariante ord: la felt være tom streng.
- Eksempelsetning: 4-8 ord, naturlig, kun presens."""


def enrich_batch(batch: list[dict], batch_idx: int, total_batches: int) -> list[dict]:
    lemmas_text = "\n".join(
        f"{i+1}. {l['lemma_es']} ({l['pos']}) — {l['hint']}"
        for i, l in enumerate(batch)
    )
    prompt = f"""Batch {batch_idx+1}/{total_batches}: Lag komplett ordinformasjon for disse {len(batch)} spanske ordene. Hver oppføring må være perfekt — en norsk lærer skal kunne godkjenne uten endringer.

For hvert ord:
- lemma_es (spansk oppslagsform, akkurat som gitt under)
- pos (ordklasse, som hintet)
- gloss_no (naturlig norsk oversettelse — for verb, bruk infinitiv "å ...")
- gender ("m", "f", "mf", eller "none" for ikke-substantiv)
- article_quirk (særegenhet som "el agua", "el día" selv om maskulin; tom streng hvis ingen)
- conjugation_applicable (true kun for verb)
- conjugation_present (hvis verb: alle 6 former; hvis ikke: alle tomme strenger)
- agreement_forms (hvis adjektiv/artikkel/noen pronomen: masc_sg/fem_sg/masc_pl/fem_pl; ellers tomme)
- plural_form (for substantiv; tom streng ellers)
- memory_hook_no (genuint nyttig, ikke banal)
- etymology_no (faktisk, kort, nevner norske/germanske kognater der de finnes)
- example_es + example_no (4-8 ord, presens, naturlig)
- cefr_level (A1/A2/B1/B2)
- frequency_rank_estimate (integer, lavt = mest vanlig)

Ordene i denne batchen:
{lemmas_text}

Returner alle {len(batch)} oppføringer som JSON etter skjemaet."""

    result, meta = generate(
        prompt=prompt,
        system=SYSTEM,
        schema=ENRICH_SCHEMA,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose=f"lemma_enrichment_batch_{batch_idx+1}",
        max_budget=2.5,
        timeout=400,
    )
    return result.get("lemmas", [])


def verify_batch(enriched: list[dict]) -> list[dict]:
    """Focused verification — critical errors only, max 10 issues per batch."""
    verify_schema = {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lemma_es": {"type": "string"},
                        "field": {"type": "string"},
                        "severity": {"type": "string", "enum": ["critical", "minor"]},
                        "problem_no": {"type": "string"},
                        "suggested_fix_no": {"type": "string"},
                    },
                    "required": ["lemma_es", "field", "severity", "problem_no", "suggested_fix_no"],
                },
            }
        },
        "required": ["issues"],
    }

    prompt = f"""Korrekturlesing: Finn KUN KRITISKE eller betydelige feil i denne batchen med spansk ordinformasjon (maks 10 issues).

VIKTIG:
- JSON-nøklene "tu", "el", "yo" osv. i conjugation_present er programmatiske — IKKE flagg manglende aksenter på dem.
- Fokuser på: feil konjugasjon, feil kjønn, oppfunnet etymologi, unaturlig norsk oversettelse, banal/ubrukelig memory hook, feil grammatisk særegenhet.
- Hopp over stilistiske småsaker.
- FYLL ALLTID UT full norsk tekst i problem_no og suggested_fix_no — aldri placeholder som «P01».

JSON å gjennomgå:
```json
{json.dumps({"lemmas": enriched}, ensure_ascii=False, indent=2)}
```

Returner issues (maks 10). Tom liste hvis alt OK."""

    result, meta = generate(
        prompt=prompt,
        system="Du er en presis korrekturleser for spansk læremiddel. Finn kun reelle feil.",
        schema=verify_schema,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose="lemma_verification",
        max_budget=1.5,
        timeout=300,
    )
    return result.get("issues", [])


def correct_batch(enriched: list[dict], issues: list[dict]) -> list[dict]:
    """Apply corrections for flagged issues."""
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

    prompt = f"""Her er en JSON med spansk ordinformasjon og en liste med flaggete problemer. Lag korrigert versjon. Ikke endre noe som ikke er flagget.

Problemer:
{issues_text}

JSON:
```json
{json.dumps({"lemmas": enriched}, ensure_ascii=False, indent=2)}
```

Returner full korrigert JSON ({len(enriched)} ord)."""

    result, meta = generate(
        prompt=prompt,
        system="Du er en presis redaktør. Fiks kun det flaggete.",
        schema=ENRICH_SCHEMA,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose="lemma_correction",
        max_budget=2.5,
        timeout=400,
    )
    return result.get("lemmas", enriched)


def main() -> None:
    seed = json.loads(SEED_PATH.read_text())
    all_seed = seed["lemmas"]
    print(f"Processing {len(all_seed)} seed lemmas in batches of {BATCH_SIZE}...")

    batches = [all_seed[i:i + BATCH_SIZE] for i in range(0, len(all_seed), BATCH_SIZE)]
    print(f"Total batches: {len(batches)}")

    all_enriched: list[dict] = []
    all_issues: list[dict] = []

    for idx, batch in enumerate(batches):
        print(f"\n[Batch {idx+1}/{len(batches)}] Enriching {len(batch)} lemmas...")
        enriched = enrich_batch(batch, idx, len(batches))
        print(f"  Got {len(enriched)} enriched entries")

        print(f"[Batch {idx+1}/{len(batches)}] Verifying...")
        issues = verify_batch(enriched)
        print(f"  {len(issues)} issues flagged")
        for iss in issues[:3]:
            print(f"    [{iss['severity']}] {iss['lemma_es']}.{iss['field']}: {iss['problem_no'][:80]}")

        if issues:
            print(f"[Batch {idx+1}/{len(batches)}] Applying corrections...")
            enriched = correct_batch(enriched, issues)
            print(f"  Corrected.")

        all_enriched.extend(enriched)
        all_issues.extend(issues)

    output = {
        "enriched_corrected": {"lemmas": all_enriched},
        "verification_issues": all_issues,
    }
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\nTotal: {len(all_enriched)} enriched, {len(all_issues)} issues (fixed)")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
