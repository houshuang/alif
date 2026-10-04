#!/usr/bin/env python3
"""Sunday reading test for the novel sprint: prepare a passage, then score a recall.

Two subcommands. Nothing here touches the learning database.

  prepare   Pick an unseen 150–200 word window from a chapter file, write the hidden
            idea-unit list + probes (Claude CLI, constrained JSON), and render a local
            HTML page with the passage, a start/done timer and the fixed recall
            instruction. Output: <out>/week-NN/{passage.txt, key.json, test.html}.

  score     Take the recall transcript (free part and prompted part as two files or
            one file with a line "=== PROMPTED ===" between them), the probe answers,
            the measured seconds and the key; ask Claude for a first-pass scoring that
            quotes the transcript span for every credited unit; append the week's row to
            weekly.jsonl and weekly.md. Hand-check every DIST/I before trusting the row.

Examples (run from this directory):
  python3 sunday_test.py prepare --chapter-file ~/src/bookifier/bilingual/input/rijal_full/02_asaad.txt \
      --week 1 --coverage 86.9
  python3 sunday_test.py score --week 1 --seconds 150 --transcript recall.txt \
      --probes probes_answers.txt --willingness yes

Protocol and rubric: README.md and recall-protocol-research.md in this folder.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARABIC_TOKEN = re.compile(r"[ء-يً-ٰٟ-ۿ]+")
DIACRITICS = re.compile(r"[ً-ْٰ]")

INSTRUCTION_EN = "Tell me everything you remember from the text, in any order. Take your time."
INSTRUCTION_NO = "Fortell alt du husker fra teksten, i den rekkefølgen du vil. Ta deg god tid."
PROMPTS = [
    "Do you remember anything else?",
    "Did anything else happen, before or after that?",
    "You mentioned X. Tell me more about that.  (once; X must be the reader's own words)",
]

KEY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary_en": {"type": "string"},
        "sentence_count": {"type": "integer"},
        "idea_units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "unit_en": {"type": "string"},
                    "arabic_span": {"type": "string"},
                    "level": {"type": "string", "enum": ["main", "supporting", "detail"]},
                    "accepted_paraphrases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "unit_en", "arabic_span", "level", "accepted_paraphrases"],
            },
        },
        "probes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_en": {"type": "string"},
                    "answer_en": {"type": "string"},
                    "targets_units": {"type": "array", "items": {"type": "integer"}},
                    "kind": {"type": "string", "enum": ["inference", "referent", "clause_relation", "detail"]},
                },
                "required": ["question_en", "answer_en", "targets_units", "kind"],
            },
        },
    },
    "required": ["summary_en", "sentence_count", "idea_units", "probes"],
}

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "code": {"type": "string", "enum": ["VG", "PART", "DIST", "NONE"]},
                    "phase": {"type": "string", "enum": ["F", "P", "none"]},
                    "transcript_span": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["id", "code", "phase", "transcript_span", "note"],
            },
        },
        "intrusions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "transcript_span": {"type": "string"},
                    "kind": {"type": "string", "enum": ["elab", "false"]},
                    "note": {"type": "string"},
                },
                "required": ["transcript_span", "kind", "note"],
            },
        },
        "probe_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "correct": {"type": "boolean"}, "note": {"type": "string"}},
                "required": ["index", "correct", "note"],
            },
        },
        "cause_tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "integer"},
                    "cause": {"type": "string", "enum": ["word_recognition", "graphemic", "syntax", "intratextual", "prior_knowledge", "unknown"]},
                    "note": {"type": "string"},
                },
                "required": ["unit_id", "cause", "note"],
            },
        },
    },
    "required": ["units", "intrusions", "probe_results", "cause_tags"],
}


def claude_json(prompt: str, schema: dict, model: str = "opus") -> dict:
    """Constrained-JSON call through the Claude CLI (free via Max). Fails loudly."""
    cmd = ["claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema), "--model", model]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise SystemExit(f"claude CLI failed ({proc.returncode}): {proc.stderr[:800]}")
    envelope = json.loads(proc.stdout)
    payload = envelope.get("structured_output") or envelope.get("result")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise SystemExit(f"unexpected CLI payload: {str(envelope)[:400]}")
    return payload


def tokens(text: str) -> list[str]:
    return ARABIC_TOKEN.findall(text)


def strip_tashkeel(text: str) -> str:
    return DIACRITICS.sub("", text)


def pick_window(text: str, min_words: int, max_words: int, seed: int | None, avoid: set[str]) -> str:
    """Pick a paragraph-aligned window of min..max Arabic words, avoiding used openings."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if tokens(p)]
    rng = random.Random(seed)
    starts = list(range(len(paras)))
    rng.shuffle(starts)
    for start in starts:
        window, count = [], 0
        for p in paras[start:]:
            window.append(p)
            count += len(tokens(p))
            if count >= min_words:
                break
        if min_words <= count <= max_words:
            opening = strip_tashkeel(" ".join(tokens(window[0])[:6]))
            if opening in avoid:
                continue
            return "\n\n".join(window)
    # Fall back: cut the longest paragraph run to max_words at a sentence boundary.
    joined = "\n\n".join(paras[starts[0]:])
    sentences = re.split(r"(?<=[.!?؟…])\s+", joined)
    out, count = [], 0
    for s in sentences:
        n = len(tokens(s))
        if count + n > max_words:
            break
        out.append(s)
        count += n
    return " ".join(out)


def used_openings(out_dir: Path) -> set[str]:
    seen = set()
    for p in out_dir.glob("week-*/passage.txt"):
        seen.add(strip_tashkeel(" ".join(tokens(p.read_text(encoding="utf-8"))[:6])))
    return seen


def render_html(passage: str, week: int, word_count: int) -> str:
    safe = passage.replace("&", "&amp;").replace("<", "&lt;").replace("\n\n", "</p><p>")
    return f"""<!doctype html>
<html lang="ar"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sunday test, week {week}</title>
<style>
:root{{--bg:#faf7f1;--fg:#1d1a16;--muted:#6d665b;--accent:#8a3b12}}
@media (prefers-color-scheme: dark){{:root{{--bg:#17150f;--fg:#efe9df;--muted:#a69d8f;--accent:#e2905e}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:Georgia,serif}}
main{{max-width:720px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:18px;color:var(--muted);font-weight:normal;margin:0 0 16px}}
#text{{display:none;direction:rtl;font-size:30px;line-height:1.9;font-family:"Amiri","Scheherazade New","Noto Naskh Arabic",serif}}
#text p{{margin:0 0 1em}}
button{{font:inherit;font-size:18px;padding:12px 22px;border-radius:10px;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer}}
#result{{margin-top:24px;font-size:18px}}
.hidden{{display:none}}
#recall{{margin-top:32px;font-size:20px;line-height:1.5}}
</style></head><body><main>
<h1>Week {week} · {word_count} words · unvocalized · no lookups</h1>
<div id="controls"><button id="start">Start reading</button></div>
<section id="text"><p>{safe}</p></section>
<div id="done" class="hidden"><button id="stop">Done</button></div>
<div id="result"></div>
<div id="recall" class="hidden">
<p><strong>Now hide this page and record.</strong></p>
<p>{INSTRUCTION_EN}</p>
<p lang="no">{INSTRUCTION_NO}</p>
</div>
<script>
let t0=null;
document.getElementById('start').onclick=()=>{{t0=performance.now();document.getElementById('controls').remove();document.getElementById('text').style.display='block';document.getElementById('done').classList.remove('hidden');}};
document.getElementById('stop').onclick=()=>{{const s=(performance.now()-t0)/1000;document.getElementById('text').style.display='none';document.getElementById('done').remove();const wpm=({word_count}/(s/60)).toFixed(0);document.getElementById('result').textContent=`${{s.toFixed(0)}} seconds · ${{wpm}} words per minute`;document.getElementById('recall').classList.remove('hidden');}};
</script></main></body></html>"""


def cmd_prepare(args) -> None:
    out_dir = Path(args.out) / f"week-{args.week:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = Path(args.chapter_file).read_text(encoding="utf-8")
    passage = pick_window(text, args.min_words, args.max_words, args.seed, used_openings(Path(args.out)))
    passage = strip_tashkeel(passage) if args.strip_tashkeel else passage
    words = len(tokens(passage))
    (out_dir / "passage.txt").write_text(passage, encoding="utf-8")
    prompt = f"""You are preparing the answer key for a weekly L2 Arabic reading test. The reader is an
adult learner who will read the Arabic passage below silently, then recall it orally in English or
Norwegian. Produce:

1. summary_en: a faithful 2–3 sentence English summary.
2. sentence_count: number of sentences in the passage.
3. idea_units: 20–35 clause-level idea units in reading order, each with a short English statement,
   the exact Arabic span it comes from, a level (main = load-bearing event/state, supporting = adds who/why/how,
   detail = colour), and 1–3 accepted paraphrases a recaller might use. A unit needs a subject and a predicate.
   Do not merge two events into one unit; do not split one clause into trivia.
4. probes: 2–3 questions on relationships a reader could plausibly miss (an inference, a pronoun referent, a
   clause relation such as negation/cause/contrast), each with the expected English answer and the unit ids it targets.

Passage (unvocalized Arabic):
<<<
{passage}
>>>"""
    key = claude_json(prompt, KEY_SCHEMA, model=args.model)
    key.update({"week": args.week, "prepared_at": datetime.now(timezone.utc).isoformat(),
                "word_count": words, "chapter_file": str(args.chapter_file),
                "coverage_pct": args.coverage, "instruction_en": INSTRUCTION_EN,
                "instruction_no": INSTRUCTION_NO, "prompts": PROMPTS})
    (out_dir / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "test.html").write_text(render_html(passage, args.week, words), encoding="utf-8")
    print(f"prepared week {args.week}: {words} words, {len(key['idea_units'])} idea units, "
          f"{len(key['probes'])} probes → {out_dir}")
    print("Do not read key.json before the test.")


def cmd_score(args) -> None:
    out_dir = Path(args.out) / f"week-{args.week:02d}"
    key = json.loads((out_dir / "key.json").read_text(encoding="utf-8"))
    passage = (out_dir / "passage.txt").read_text(encoding="utf-8")
    transcript = Path(args.transcript).read_text(encoding="utf-8")
    if "=== PROMPTED ===" in transcript:
        free, prompted = transcript.split("=== PROMPTED ===", 1)
    else:
        free, prompted = transcript, ""
    probes_text = Path(args.probes).read_text(encoding="utf-8") if args.probes else ""
    units_json = json.dumps(key["idea_units"], ensure_ascii=False, indent=1)
    probes_json = json.dumps(key["probes"], ensure_ascii=False, indent=1)
    prompt = f"""Score an L1 oral recall of an Arabic passage against a fixed idea-unit list.

Rules (apply literally):
- One code per unit, first adequate mention only. VG = verbatim or faithful paraphrase (accepted paraphrases
  count; a mangled proper name that keeps the idea counts). PART = core predicate right but one required element
  missing or vague. DIST = the unit is mentioned but its meaning is wrong (wrong agent/patient, missed or added
  negation, wrong pronoun referent, wrong time or causal link, wrong required detail). NONE = not mentioned.
- phase = F if the first adequate mention is in the FREE section, P if only in the PROMPTED section, none for NONE.
- Quote the exact transcript span that justifies every VG/PART/DIST. Never invent spans. If unsure, prefer NONE.
- intrusions: content not in the passage. kind = elab (plausible inference) or false (contradicts or invents).
- probe_results: judge each probe answer against the expected answer; paraphrase counts.
- cause_tags: for every DIST and false intrusion, tag the likely cause only when evident from the Arabic
  (word_recognition, graphemic, syntax, intratextual, prior_knowledge); otherwise "unknown".

PASSAGE:
<<<
{passage}
>>>

IDEA UNITS:
{units_json}

PROBES (expected answers):
{probes_json}

FREE RECALL TRANSCRIPT:
<<<
{free.strip()}
>>>

PROMPTED RECALL TRANSCRIPT (after the first neutral prompt; may be empty):
<<<
{prompted.strip()}
>>>

PROBE ANSWERS (in order; may be empty):
<<<
{probes_text.strip()}
>>>"""
    scored = claude_json(prompt, SCORE_SCHEMA, model=args.model)
    units = {u["id"]: u for u in key["idea_units"]}
    total = len(units)
    credit = {"VG": 1.0, "PART": 0.5, "DIST": 0.0, "NONE": 0.0}
    f_score = sum(credit[u["code"]] for u in scored["units"] if u["phase"] == "F")
    p_score = sum(credit[u["code"]] for u in scored["units"] if u["phase"] == "P")
    main_ids = {i for i, u in units.items() if u["level"] == "main"}
    main_hit = sum(1 for u in scored["units"] if u["id"] in main_ids and u["code"] in {"VG", "PART"})
    dist = sum(1 for u in scored["units"] if u["code"] == "DIST")
    intr = len(scored["intrusions"])
    q_ok = sum(1 for p in scored["probe_results"] if p["correct"])
    wpm = round(key["word_count"] / (args.seconds / 60.0), 1) if args.seconds else None
    row = {
        "week": args.week, "date": str(date.today()), "word_count": key["word_count"],
        "seconds": args.seconds, "wpm": wpm,
        "units_total": total, "F": round(f_score, 1), "P": round(p_score, 1),
        "F_pct": round(100 * f_score / total, 1), "P_pct": round(100 * p_score / total, 1),
        "FP_pct": round(100 * (f_score + p_score) / total, 1),
        "main_share": round(main_hit / len(main_ids), 2) if main_ids else None,
        "D": dist, "I": intr, "Q": f"{q_ok}/{len(scored['probe_results'])}",
        "willingness": args.willingness, "coverage_pct": key.get("coverage_pct"),
        "chapters_finished": args.chapters_finished, "lookups_per_100": args.lookups_per_100,
        "targets_introduced": args.targets_introduced, "old_word_7d_clean_pct": args.old_word_7d,
        "notes": args.notes or "",
        "hand_checked": False,
    }
    (out_dir / "score.json").write_text(json.dumps({"row": row, "scored": scored}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (HERE / "weekly.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    md = HERE / "weekly.md"
    if not md.exists():
        md.write_text("# Weekly log\n\n| week | date | words | wpm | F% | P% | F+P% | main | D | I | Q | will | cov% | ch | notes |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
    with md.open("a", encoding="utf-8") as fh:
        fh.write(f"| {row['week']} | {row['date']} | {row['word_count']} | {row['wpm']} | {row['F_pct']} | {row['P_pct']} | {row['FP_pct']} | {row['main_share']} | {row['D']} | {row['I']} | {row['Q']} | {row['willingness']} | {row['coverage_pct']} | {row['chapters_finished']} | {row['notes']} |\n")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    print("\nDIST / intrusion decisions to hand-check:")
    for u in scored["units"]:
        if u["code"] == "DIST":
            print(f"  DIST unit {u['id']}: {units[u['id']]['unit_en']} ← \"{u['transcript_span']}\" ({u['note']})")
    for i in scored["intrusions"]:
        print(f"  I-{i['kind']}: \"{i['transcript_span']}\" ({i['note']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.environ.get("SUNDAY_TEST_DIR", str(Path.home() / "src/bookifier/bilingual/input/rijal_full/sunday")),
                    help="Where week folders live (outside the repo: passages are book content)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--chapter-file", required=True)
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--min-words", type=int, default=150)
    p.add_argument("--max-words", type=int, default=200)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--coverage", type=float, default=None, help="Alif coverage %% of the chapter (from reading_readiness)")
    p.add_argument("--strip-tashkeel", action="store_true", default=True)
    p.add_argument("--model", default="opus")
    p.set_defaults(func=cmd_prepare)
    s = sub.add_parser("score")
    s.add_argument("--week", type=int, required=True)
    s.add_argument("--seconds", type=float, required=True)
    s.add_argument("--transcript", required=True)
    s.add_argument("--probes", default=None)
    s.add_argument("--willingness", choices=["yes", "meh", "no"], required=True)
    s.add_argument("--chapters-finished", type=int, default=0)
    s.add_argument("--lookups-per-100", type=float, default=None)
    s.add_argument("--targets-introduced", type=int, default=None)
    s.add_argument("--old-word-7d", type=float, default=None)
    s.add_argument("--notes", default=None)
    s.add_argument("--model", default="opus")
    s.set_defaults(func=cmd_score)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
