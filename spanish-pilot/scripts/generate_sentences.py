"""Generate pilot sentences in batches using the enriched lemma set.

Design:
  - Read content/lemmas.json to get full vocabulary
  - Generate N_PER_BATCH=15 sentences per batch, TOTAL_BATCHES=10 batches = 150 sentences
  - Each batch has variety constraints (different subjects, verbs, noun phrases)
  - Verify each batch, apply corrections, merge
  - Present tense only
  - Only vocabulary in the provided lemma set
  - Each sentence has word mapping + 3 distractors
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "/Users/stian/src/limbic")
from limbic.cerebellum.claude_cli import generate

ROOT = Path(__file__).resolve().parent.parent
LEMMAS_PATH = ROOT / "content" / "lemmas.json"
OUT_PATH = ROOT / "content" / "sentences.json"

N_PER_BATCH = 15
TOTAL_BATCHES = 10  # 150 sentences total

BATCH_THEMES = [
    "hverdagssetninger om familie og venner",
    "setninger om mat og drikke",
    "setninger om skole og arbeid",
    "setninger om hjem og ting",
    "setninger med ulike subjekter (yo/tú/él/ella/nosotros/ellos)",
    "setninger med adjektiver og kjønnssamsvar",
    "setninger om by, gate, land",
    "setninger med tidsuttrykk (hoy, ahora, siempre)",
    "setninger med negasjon (no) og konjunksjoner (y, pero, porque)",
    "blandede setninger med forskjellige verb og substantiv",
]


SENTENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
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


SYSTEM = """Du er ekspert på spansk og norsk. Du lager øvingsmateriell for norske elever som lærer spansk på begynnernivå (A1-A2).

HARDE REGLER:
- ALLE setninger MÅ bruke KUN ord fra vokabularlisten. Ingen andre ord tillatt.
- Kun presens indikativ. Ingen andre tider.
- Hver setning 4-8 ord.
- Naturlige setninger — ikke stive, ikke ord-for-ord-oversatte.
- Norsk oversettelse MÅ matche spansk verbet presist. ALDRI bytt verb i oversettelsen for å «reparere» en rar setning — lag heller en annen setning.
  FEIL EKSEMPEL: «El niño come agua» → «Gutten drikker vann». FEIL! «come» = spiser, ikke drikker.
- Ikke par verb med rare objekter (f.eks. «spise vann», «snakke hus»).
- Distractors skal være PLAUSIBLE feil (feil subjekt, feil bøying, feil objekt) — ikke tilfeldige setninger.
- Ingen engelsk tekst noe sted.
- word_mapping: for HVERT ord i setningen (inkludert artikler og preposisjoner), marker position (0-indeks), overflateform, lemma_es (må matche en lemma i vokabularlisten), og kort grammatisk note (f.eks. «presens.3sg», «fem.sg», «maskulin.pl»)."""


def build_compact_vocab(lemmas: list[dict]) -> str:
    """Compact vocab listing for prompt — just lemma + pos + gloss + forms."""
    lines = []
    for l in lemmas:
        forms = [l["lemma_es"]]
        if l.get("conjugation_applicable"):
            c = l.get("conjugation_present") or {}
            forms.extend([c.get("yo", ""), c.get("tu", ""), c.get("el", ""), c.get("nosotros", ""), c.get("vosotros", ""), c.get("ellos", "")])
        if l.get("plural_form"):
            forms.append(l["plural_form"])
        agr = l.get("agreement_forms") or {}
        if agr:
            forms.extend([v for v in [agr.get("masc_sg"), agr.get("fem_sg"), agr.get("masc_pl"), agr.get("fem_pl")] if v])
        forms = [f for f in dict.fromkeys(forms) if f]
        lines.append(f"{l['lemma_es']} [{l['pos']}] = {l['gloss_no']} ({'/'.join(forms)})")
    return "\n".join(lines)


def generate_batch(lemmas: list[dict], batch_idx: int, theme: str, existing_samples: list[str]) -> list[dict]:
    vocab_text = build_compact_vocab(lemmas)
    allowed = [l["lemma_es"] for l in lemmas]
    avoid = "\n".join(f"- {s}" for s in existing_samples[-20:])
    avoid_block = f"\n\nUnngå å generere setninger svært like disse (som allerede finnes):\n{avoid}" if existing_samples else ""

    prompt = f"""Batch {batch_idx+1}/{TOTAL_BATCHES} — TEMA: {theme}

Lag {N_PER_BATCH} øvingssetninger på spansk.

VARIASJON:
- Ikke start alle med «El niño» eller «Yo» — varier subjekt
- Bruk både entall og flertall
- Bruk både maskulin og feminin
- Inkluder noen setninger med negasjon, konjunksjon, eller adjektiv+substantiv
- Forskjellige verb brukes på tvers av setningene

Vokabularliste (kun disse ordene tillatt, med alle deres bøyningsformer):
{vocab_text}

{avoid_block}

For hver setning, fyll ut:
- es: spansk tekst, 4-8 ord
- no: naturlig norsk oversettelse (bokmål)
- word_mapping: for hvert ord (inkludert artikler/preposisjoner), angi position (0-indeks fra starten), form, lemma_es (må være i listen: {allowed[:20]}...), grammatical_note
- distractors_no: 3 plausible-men-gale norske oversettelser (feil subjekt/bøying/objekt)
- difficulty_rank: integer (lav = lett)

Returner JSON med {N_PER_BATCH} setninger."""

    result, meta = generate(
        prompt=prompt,
        system=SYSTEM,
        schema=SENTENCE_SCHEMA,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose=f"sentence_gen_batch_{batch_idx+1}",
        max_budget=2.5,
        timeout=400,
    )
    return result.get("sentences", [])


def verify_batch(lemmas: list[dict], sentences: list[dict]) -> list[dict]:
    allowed = [l["lemma_es"] for l in lemmas]
    verify_schema = {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sentence_index": {"type": "integer"},
                        "field": {"type": "string"},
                        "severity": {"type": "string", "enum": ["critical", "minor"]},
                        "problem_no": {"type": "string"},
                        "suggested_fix_no": {"type": "string"},
                    },
                    "required": ["sentence_index", "field", "severity", "problem_no", "suggested_fix_no"],
                },
            }
        },
        "required": ["issues"],
    }

    prompt = f"""Korrekturlesing: Finn KUN KRITISKE feil i disse {len(sentences)} spanske øvingssetningene (maks 10 issues).

KRITISKE feil (rapporter disse):
1. Norsk oversettelse matcher IKKE spansk verb/subjekt (f.eks. «come» oversatt som «drikker»)
2. Spansk grammatikk-feil (feil bøying, feil kongruens)
3. Ord utenfor vokabularlisten
4. Brukt annen tempus enn presens
5. word_mapping.lemma_es refererer til lemma som ikke finnes i listen
6. Distractors er tilfeldige setninger (ikke plausible feil)
7. Verb+objekt som gir semantisk meningsløs setning

FYLL ALLTID UT full norsk tekst i problem_no/suggested_fix_no. Aldri placeholder.

Tillatte lemmaer: {allowed}

Setninger:
```json
{json.dumps({"sentences": sentences}, ensure_ascii=False, indent=2)}
```

Returner maks 10 issues. Tom liste hvis alt OK."""

    result, meta = generate(
        prompt=prompt,
        system="Du er pedantisk korrekturleser. Finn kun reelle feil.",
        schema=verify_schema,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose="sentence_verification",
        max_budget=1.5,
        timeout=300,
    )
    return result.get("issues", [])


def correct_batch(lemmas: list[dict], sentences: list[dict], issues: list[dict]) -> list[dict]:
    if not issues:
        return sentences
    allowed = [l["lemma_es"] for l in lemmas]
    issues_text = "\n".join(
        f"- s{iss['sentence_index']}.{iss['field']} [{iss['severity']}]: {iss['problem_no']}\n  Forslag: {iss['suggested_fix_no']}"
        for iss in issues
    )
    prompt = f"""Lag korrigert versjon av disse setningene. Fiks ALLE flaggete problemer, behold resten uendret.

Tillatte lemmaer: {allowed}

Flaggete problemer:
{issues_text}

Opprinnelige setninger:
```json
{json.dumps({"sentences": sentences}, ensure_ascii=False, indent=2)}
```

Returner full korrigert JSON med {len(sentences)} setninger."""

    result, meta = generate(
        prompt=prompt,
        system="Du er en presis redaktør.",
        schema=SENTENCE_SCHEMA,
        model="claude-sonnet-4-5-20250929",
        project="spanish-pilot",
        purpose="sentence_correction",
        max_budget=2.5,
        timeout=400,
    )
    return result.get("sentences", sentences)


def main() -> None:
    data = json.loads(LEMMAS_PATH.read_text())
    source = data.get("enriched_corrected") or data.get("enriched") or {"lemmas": []}
    lemmas = source["lemmas"]
    print(f"Using {len(lemmas)} lemmas to generate {N_PER_BATCH * TOTAL_BATCHES} sentences")

    all_sentences: list[dict] = []
    all_issues: list[dict] = []
    all_samples: list[str] = []  # used for "avoid similar" hints

    for idx in range(TOTAL_BATCHES):
        theme = BATCH_THEMES[idx % len(BATCH_THEMES)]
        print(f"\n[Batch {idx+1}/{TOTAL_BATCHES}] Theme: {theme}")
        print(f"  Generating {N_PER_BATCH} sentences...")
        batch = generate_batch(lemmas, idx, theme, all_samples)
        print(f"  Got {len(batch)} sentences")

        print(f"[Batch {idx+1}/{TOTAL_BATCHES}] Verifying...")
        issues = verify_batch(lemmas, batch)
        print(f"  {len(issues)} issues flagged")
        for iss in issues[:3]:
            print(f"    [{iss['severity']}] s{iss['sentence_index']}.{iss['field']}: {iss['problem_no'][:80]}")

        if issues:
            print(f"[Batch {idx+1}/{TOTAL_BATCHES}] Applying corrections...")
            batch = correct_batch(lemmas, batch, issues)

        all_sentences.extend(batch)
        all_issues.extend(issues)
        all_samples.extend(s["es"] for s in batch)

        # Save intermediate (in case of crash)
        output = {
            "sentences_corrected": {"sentences": all_sentences},
            "verification_issues": all_issues,
        }
        OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    print(f"\nTotal: {len(all_sentences)} sentences, {len(all_issues)} issues (fixed)")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
