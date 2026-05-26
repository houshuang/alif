"""Generate short Latin reading paragraphs that cover LLPSI Familia Romana
vocabulary chapter-by-chapter (a "coverage reader").

The premise (see `polyglot/CLAUDE.md` Hard Invariant 6 — scaffold confirmation):
LLPSI seed lemmas are imported as `knowledge_state='known', source='llpsi_known'`
with NO FSRS card. They get `confirmed_at` stamped the first time the learner
greens them in a sentence; red lapses them into the acquisition pipeline.

Today, scaffold confirmation only happens incidentally — when an LLPSI word
shows up as collateral in a sentence generated for some other retrieval target.
With ~1585 LLPSI seeds and a small acquisition pool, that's too slow. This
script generates short coverage paragraphs targeting each LLPSI chapter, so the
learner can scaffold-confirm the whole list by reading ~5000 Latin words
(~1/4 the length of Familia Romana) instead of re-reading the textbook.

Driven by `data/vocab/llpsi_fr.tsv` (the seed file used by `import_latin_vocab.py
--phase llpsi`) for chapter membership — no DB query needed for the generation
itself. Output is printed and written to a markdown report. Seeding the result
into the polyglot reader is a separate step (run after the texts look good).

USAGE
    polyglot/.venv/bin/python scripts/generate_llpsi_coverage_texts.py \\
        --chapter 1 --passes 3
    polyglot/.venv/bin/python scripts/generate_llpsi_coverage_texts.py \\
        --all --coverage-threshold 0.85
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("llpsi_coverage")

# Resolve the polyglot package root regardless of cwd.
THIS = Path(__file__).resolve()
PKG_ROOT = THIS.parent.parent  # .../polyglot
REPO_ROOT = PKG_ROOT.parent     # .../alif (or worktree root)
sys.path.insert(0, str(PKG_ROOT))

from app.services import llm_cli  # noqa: E402
from app.services.languages.la import LatinProvider, _normalize_latin  # noqa: E402

try:
    import simplemma  # noqa: E402
    _HAVE_SIMPLEMMA = True
except ImportError:
    _HAVE_SIMPLEMMA = False


def _simplemma_bare(surface: str) -> str | None:
    """simplemma lemma (lowercase, macron-stripped, u/i-folded) or None."""
    if not _HAVE_SIMPLEMMA or not surface:
        return None
    try:
        lemma = simplemma.lemmatize(surface, lang="la", greedy=True)
    except Exception:
        return None
    if not lemma:
        return None
    return _normalize_latin(lemma)

DEFAULT_TSV = PKG_ROOT / "data" / "vocab" / "llpsi_fr.tsv"
DEFAULT_REPORT_DIR = REPO_ROOT / "research"


# ─── TSV loading ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LlpsiEntry:
    """One LLPSI chapter vocab item.

    `bare` is the primary lookup key (TSV surface, normalised). `match_bares`
    is the set of bares that should count as a coverage hit — typically the
    union of the TSV-surface bare and the LatinCy-isolated-lemma bare,
    because LatinCy is famously context-finicky (CLAUDE.md: "occasionally
    fails to reduce an inflection") and the same word will sometimes
    lemmatise to its surface and sometimes to the canonical lemma in real
    prose. We accept either side as confirmation.
    """
    bare: str
    surface: str
    gloss: str
    chapter: int
    match_bares: frozenset[str] = field(default_factory=frozenset)


def load_llpsi(tsv_path: Path) -> list[LlpsiEntry]:
    """Read llpsi_fr.tsv → list of entries (raw surfaces — NOT canonicalised).

    The TSV is the LLPSI back-of-chapter vocab list in its native form: most
    entries are dictionary citation forms (`fluvius`, `ire`) but several are
    inflected surfaces (`est`, `sunt`, `multi`) or come with question marks
    (`ubi?`, `quid?`). `import_latin_vocab.py` runs them through LatinCy at
    import time so the DB stores canonical lemmas. We canonicalise here too
    via `canonicalise_targets` once the LatinProvider is loaded — running it
    inside `load_llpsi` would force LatinCy load at import time and break the
    fail-fast diagnostic in main().
    """
    out: list[LlpsiEntry] = []
    with tsv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            lemma = (row.get("lemma") or "").strip()
            chapter_raw = (row.get("chapter") or "").strip()
            if not lemma or not chapter_raw:
                continue
            try:
                chapter = int(chapter_raw)
            except ValueError:
                continue
            bare = _normalize_latin(lemma)
            out.append(LlpsiEntry(
                bare=bare,
                surface=lemma,
                gloss=(row.get("gloss") or "").strip(),
                chapter=chapter,
                match_bares=frozenset({bare}),
            ))
    return out


def augment_match_bares(
    entries: list[LlpsiEntry],
    provider: LatinProvider,
) -> list[LlpsiEntry]:
    """Expand each entry's ``match_bares`` with the LatinCy-isolated lemma so
    coverage measurement accepts either the TSV surface or LatinCy's
    canonical (e.g. ``est`` ↔ ``sum``, ``multi`` ↔ ``multus``).

    Deliberately does NOT replace the primary ``bare`` or dedupe entries —
    each TSV row stays its own target, because:
      - LatinCy isolation gives nonsense for some words (`pensum`→`pendo`),
        so picking it as the canonical would corrupt the target list.
      - LLPSI treats inflection-variants as separate vocab items (``est``,
        ``sunt`` are both chapter-1 entries); the learning task is to
        recognise both, and the chapter coverage count should reflect that.
    """
    augmented: list[LlpsiEntry] = []
    for e in entries:
        # Strip non-alpha (?, !) so `ubi?` and `quid?` lemmatise cleanly.
        cleaned = "".join(c for c in e.surface if c.isalpha() or c in "-")
        bares = set(e.match_bares)
        if cleaned:
            bares.add(_normalize_latin(cleaned))
            try:
                cand = provider.lemmatize(cleaned, context=None)
                if cand.lemma_bare:
                    bares.add(cand.lemma_bare)
                else:
                    bares.add(_normalize_latin(cand.lemma))
            except Exception:
                pass
            # simplemma is a flat lookup table — more aggressive about
            # inflection reduction than LatinCy in isolation, and crucially
            # doesn't drag `pensum` to its verb root `pendo`.
            sm = _simplemma_bare(cleaned)
            if sm:
                bares.add(sm)
        augmented.append(LlpsiEntry(
            bare=e.bare,
            surface=e.surface,
            gloss=e.gloss,
            chapter=e.chapter,
            match_bares=frozenset(bares),
        ))
    return augmented


def index_by_chapter(entries: list[LlpsiEntry]) -> dict[int, list[LlpsiEntry]]:
    by_ch: dict[int, list[LlpsiEntry]] = {}
    for e in entries:
        by_ch.setdefault(e.chapter, []).append(e)
    for ch in by_ch:
        by_ch[ch].sort(key=lambda e: e.surface)
    return by_ch


# ─── Prompt construction ─────────────────────────────────────────────────────


def _format_word_list(entries: list[LlpsiEntry], with_glosses: bool = True) -> str:
    if with_glosses:
        return "\n".join(
            f"- {e.surface} — {e.gloss}" if e.gloss else f"- {e.surface}"
            for e in entries
        )
    return ", ".join(e.surface for e in entries)


def build_initial_prompt(
    chapter: int,
    targets: list[LlpsiEntry],
    scaffold: list[LlpsiEntry],
    *,
    sentences: int,
) -> str:
    """First-pass prompt: maximize coverage of `targets` in natural Latin prose,
    using `scaffold` (earlier-chapter vocab) for connective tissue.
    """
    target_block = _format_word_list(targets)
    # Cap the scaffold list to keep the prompt compact — earlier-chapter words
    # are widely known to the LLM and the constraint is mostly to *avoid*
    # later-chapter forms, not to teach scaffold.
    if len(scaffold) > 250:
        scaffold = scaffold[:250]
    scaffold_block = _format_word_list(scaffold, with_glosses=False) if scaffold else "(none — this is chapter 1)"

    return f"""You are writing a short Latin reading passage for an intermediate
learner who has just finished LLPSI (Lingua Latina per se Illustrata: Familia
Romana) Chapter {chapter}. The passage is a *coverage tool* — its job is to
expose the learner to as many of the listed TARGET WORDS as you can naturally
weave into fluent, readable Latin prose. The learner has seen these words in
the textbook; reading them in a fresh context confirms recognition.

TARGET WORDS (LLPSI Chapter {chapter}, lemma + gloss):
{target_block}

ASSUMED-KNOWN VOCABULARY (lemmas from Chapters 1–{chapter - 1 if chapter > 1 else 0}, available for connective tissue):
{scaffold_block}

CONSTRAINTS
1. Modern reading Latin orthography (LLPSI / OUP intermediate convention):
   - NO macrons under any circumstance, not even on sentence-initial words
     (write "Marcus" NOT "Mārcus", "vita" NOT "vīta").
   - Use "v" for consonantal v: vir, vocabulum, navis, novus, servus, vita,
     volo, video, vox.
   - DO NOT use "j" — keep "i" for both vocalic and consonantal positions:
     iuvenis (NOT juvenis), Iulius (NOT Julius), Iulia (NOT Julia), iam (NOT
     jam), eius (NOT ejus), maior (NOT major), ius (NOT jus), iuvo (NOT juvo).
   - Digraph exceptions: keep "u" (not "v") after q, g, s + vowel: aqua, qui,
     lingua, pinguis, suadeo, suavis (but second u in suavis is intervocalic
     and becomes v: write "suavis" NOT "suauis").
   - Write Marcus, vocabulum, vir, iuvenis, navis, Iulia, eius — NOT Mārcus,
     uocabulum, uir, iuuenis, nauis, juvenis, Julia, ejus.
2. Cover as many TARGET WORDS as you can while keeping the prose natural. A
   forced vocabulary-list reading is worse than a shorter natural passage.
3. Length: about {sentences} sentences, roughly 80–140 Latin words total.
4. Use ONLY words from TARGET, ASSUMED-KNOWN, common closed-class words
   (et, sed, est, in, ad, etc.), and standard LLPSI proper names where they fit
   (Iulius, Aemilia, Marcus, Quintus, Iulia, Syra, Davus, Medus, Roma, Tusculum).
   Avoid LLPSI vocabulary from chapters later than {chapter}.
5. Inflect to fit grammar naturally — every form should be correct Latin. Do
   not insert vocabulary in their nominative citation form just to tick boxes.
6. Write a coherent micro-scene — a description, a household moment, a small
   action — not a string of disconnected facts.
7. Return ONLY the Latin paragraph. No English translation, no commentary, no
   chapter heading. Plain prose, sentences separated by spaces or newlines.
"""


def build_remainder_prompt(
    chapter: int,
    previous_paragraphs: list[str],
    still_missing: list[LlpsiEntry],
    scaffold: list[LlpsiEntry],
    *,
    sentences: int,
) -> str:
    """Subsequent-pass prompt: cover the remaining TARGET words the earlier
    paragraphs missed, in a fresh micro-scene."""
    target_block = _format_word_list(still_missing)
    if len(scaffold) > 250:
        scaffold = scaffold[:250]
    scaffold_block = _format_word_list(scaffold, with_glosses=False) if scaffold else "(none — this is chapter 1)"
    earlier = "\n\n".join(f'"{p.strip()}"' for p in previous_paragraphs)
    return f"""You earlier wrote these Latin paragraph(s) covering LLPSI Chapter
{chapter} vocabulary:

{earlier}

The following TARGET WORDS from Chapter {chapter} are still NOT covered. Write
another short, natural Latin paragraph that uses as many of these as you can.
The new paragraph can describe a different scene, continue the same scene, or
shift perspective — choose whatever lets you naturally cover the most missing
words.

STILL-MISSING TARGET WORDS (LLPSI Chapter {chapter}):
{target_block}

ASSUMED-KNOWN VOCABULARY (Chapters 1–{chapter - 1 if chapter > 1 else 0}, plus the words you already used above):
{scaffold_block}

CONSTRAINTS (same as before)
1. Modern reading orthography (LLPSI / OUP intermediate): NO macrons (even on
   sentence-initial words); "v" for consonantal v (vir, navis, vita, vocabulum,
   novus); KEEP "i" for consonantal i — do NOT use "j" (iuvenis NOT juvenis;
   Iulius NOT Julius; iam NOT jam; eius NOT ejus; maior NOT major). Digraph
   exceptions: u stays after q/g/s + vowel (aqua, lingua, suadeo). NEVER write
   Mārcus, uocabulum, uir, iuuenis, nauis, juvenis, Julia, ejus.
2. Roughly {sentences} sentences, 60–120 Latin words.
3. Use ONLY words from STILL-MISSING, ASSUMED-KNOWN, common closed-class words,
   and standard LLPSI proper names. Avoid post-Chapter-{chapter} vocab.
4. Natural prose, not a vocabulary list.
5. Return ONLY the Latin paragraph.
"""


# ─── LLM call ────────────────────────────────────────────────────────────────


def call_latin_prose(prompt: str, model: str, timeout_s: int = 180) -> str | None:
    """Wrap llm_cli.call_text and strip common preamble noise."""
    result = llm_cli.call_text(
        prompt=prompt,
        model=model,
        timeout_s=timeout_s,
        log_context="llpsi_coverage",
    )
    if not result:
        return None
    return _strip_preamble(result)


def _strip_preamble(text: str) -> str:
    """Best-effort: drop any "Here is..." preamble or trailing English notes."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # If a line is mostly ASCII English (and not classical Latin), drop it.
    def is_likely_english(line: str) -> bool:
        low = line.lower().strip()
        if not low:
            return True
        markers = (
            "here is", "here's", "here are", "note:", "translation:",
            "english:", "latin:", "paragraph:", "this paragraph",
            "(", "covers", "**",
        )
        return any(low.startswith(m) for m in markers)
    cleaned: list[str] = []
    for ln in lines:
        if cleaned == [] and is_likely_english(ln):
            continue
        cleaned.append(ln)
    # Drop trailing English commentary
    while cleaned and is_likely_english(cleaned[-1]):
        cleaned.pop()
    return "\n".join(cleaned).strip()


# ─── Coverage measurement ────────────────────────────────────────────────────


def measure_coverage(
    text: str,
    targets: list[LlpsiEntry],
    provider: LatinProvider,
) -> set[str]:
    """Lemmatise `text` and return the set of primary `bare` values of
    `targets` that the text covers.

    Each unique surface is lemmatised in the context of its sentence (LatinCy
    needs the context for disambiguation). For each token we test BOTH the
    surface bare and the lemma bare against each target's `match_bares` set —
    LatinCy is context-finicky enough that a target's word can show up either
    way (e.g. `pauci` → sometimes `pauci`, sometimes `paucus`). Accepting
    either is the only honest measure of "did the learner see this word."
    """
    bare_to_targets: dict[str, set[str]] = {}
    for t in targets:
        for mb in t.match_bares:
            bare_to_targets.setdefault(mb, set()).add(t.bare)

    covered_primary: set[str] = set()
    tokens = provider.tokenize(text)
    sentences: list[list[int]] = []
    current: list[int] = []
    for i, tok in enumerate(tokens):
        if tok.is_punctuation and any(p in tok.surface for p in ".!?"):
            if current:
                sentences.append(current)
                current = []
        else:
            current.append(i)
    if current:
        sentences.append(current)

    for sent_idxs in sentences:
        ctx_tokens = [tokens[i].surface for i in sent_idxs]
        context = " ".join(ctx_tokens)
        for i in sent_idxs:
            surface = tokens[i].surface
            if not surface or not any(c.isalpha() for c in surface):
                continue
            surface_bare = _normalize_latin(surface)
            try:
                cand = provider.lemmatize(surface, context=context)
                lemma_bare = cand.lemma_bare or _normalize_latin(cand.lemma)
            except Exception as exc:
                LOG.debug("lemmatize failed for %r: %s", surface, exc)
                lemma_bare = surface_bare
            keys = {surface_bare, lemma_bare}
            sm = _simplemma_bare(surface)
            if sm:
                keys.add(sm)
            for key in keys:
                if key in bare_to_targets:
                    covered_primary.update(bare_to_targets[key])
    return covered_primary


# ─── Chapter pipeline ────────────────────────────────────────────────────────


@dataclass
class ChapterResult:
    chapter: int
    target_count: int
    paragraphs: list[str] = field(default_factory=list)
    covered_per_pass: list[list[str]] = field(default_factory=list)
    final_covered: set[str] = field(default_factory=set)
    final_missing: list[LlpsiEntry] = field(default_factory=list)
    word_count: int = 0
    elapsed_s: float = 0.0
    error: str | None = None


def run_chapter(
    chapter: int,
    targets: list[LlpsiEntry],
    scaffold: list[LlpsiEntry],
    *,
    passes: int,
    coverage_threshold: float,
    sentences_per_pass: int,
    model: str,
    provider: LatinProvider,
) -> ChapterResult:
    target_bares = {e.bare for e in targets}
    bare_to_entry = {e.bare: e for e in targets}
    result = ChapterResult(chapter=chapter, target_count=len(targets))
    started = time.monotonic()
    covered_total: set[str] = set()

    for pass_n in range(1, passes + 1):
        still_missing_bares = target_bares - covered_total
        if not still_missing_bares:
            break
        coverage_ratio = len(covered_total) / len(target_bares) if target_bares else 1.0
        if coverage_ratio >= coverage_threshold:
            LOG.info("chapter %d: hit threshold %.0f%% at pass %d (%.0f%%)",
                     chapter, coverage_threshold * 100, pass_n - 1, coverage_ratio * 100)
            break
        still_missing = [bare_to_entry[b] for b in sorted(still_missing_bares)]
        # measure_coverage works against the FULL target list (it needs the
        # match_bares to interpret text), not just the still-missing slice —
        # but coverage_this_pass below intersects with target_bares afterwards.

        if pass_n == 1:
            prompt = build_initial_prompt(
                chapter=chapter,
                targets=targets,
                scaffold=scaffold,
                sentences=sentences_per_pass,
            )
        else:
            prompt = build_remainder_prompt(
                chapter=chapter,
                previous_paragraphs=result.paragraphs,
                still_missing=still_missing,
                scaffold=scaffold,
                sentences=sentences_per_pass,
            )

        LOG.info("chapter %d pass %d: %d still missing (covered %d/%d)",
                 chapter, pass_n, len(still_missing), len(covered_total), len(target_bares))
        paragraph = call_latin_prose(prompt, model=model)
        if not paragraph:
            LOG.warning("chapter %d pass %d: LLM returned nothing", chapter, pass_n)
            result.covered_per_pass.append([])
            continue

        covered_this_pass = measure_coverage(paragraph, targets, provider)
        new_words = covered_this_pass - covered_total
        result.paragraphs.append(paragraph)
        result.covered_per_pass.append(sorted(new_words))
        covered_total |= covered_this_pass
        LOG.info("chapter %d pass %d: +%d new (now %d/%d = %.0f%%)",
                 chapter, pass_n, len(new_words),
                 len(covered_total), len(target_bares),
                 100 * len(covered_total) / len(target_bares) if target_bares else 0)

    result.final_covered = covered_total
    result.final_missing = [bare_to_entry[b] for b in sorted(target_bares - covered_total)]
    result.word_count = sum(
        len([t for t in provider.tokenize(p) if not t.is_punctuation])
        for p in result.paragraphs
    )
    result.elapsed_s = time.monotonic() - started
    return result


# ─── Reporting ───────────────────────────────────────────────────────────────


def format_chapter_md(r: ChapterResult) -> str:
    ratio = (len(r.final_covered) / r.target_count) if r.target_count else 0.0
    lines = [
        f"## Chapter {r.chapter}",
        "",
        f"- Targets: **{r.target_count}**",
        f"- Covered: **{len(r.final_covered)} ({ratio:.0%})**",
        f"- Paragraphs: {len(r.paragraphs)}",
        f"- Word count: {r.word_count}",
        f"- Time: {r.elapsed_s:.1f}s",
        "",
    ]
    for i, para in enumerate(r.paragraphs, start=1):
        new_words = r.covered_per_pass[i - 1] if i - 1 < len(r.covered_per_pass) else []
        lines.append(f"### Pass {i} — covered {len(new_words)} new word(s)")
        lines.append("")
        lines.append("```")
        lines.append(para.strip())
        lines.append("```")
        if new_words:
            lines.append("")
            lines.append(f"_New coverage:_ {', '.join(new_words)}")
        lines.append("")
    if r.final_missing:
        lines.append("### Still missing")
        lines.append("")
        for m in r.final_missing:
            lines.append(f"- `{m.surface}` — {m.gloss}")
        lines.append("")
    return "\n".join(lines)


def format_summary_md(results: list[ChapterResult], cfg: dict) -> str:
    total_targets = sum(r.target_count for r in results)
    total_covered = sum(len(r.final_covered) for r in results)
    total_words = sum(r.word_count for r in results)
    total_elapsed = sum(r.elapsed_s for r in results)
    overall_ratio = (total_covered / total_targets) if total_targets else 0.0
    lines = [
        "# LLPSI Familia Romana — Coverage Reader (generated)",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(cfg, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Summary",
        "",
        f"- Chapters processed: **{len(results)}**",
        f"- Total target lemmas: **{total_targets}**",
        f"- Total covered: **{total_covered} ({overall_ratio:.0%})**",
        f"- Total Latin words generated: **{total_words}**",
        f"- Total LLM time: **{total_elapsed:.1f}s**",
        "",
        "Per-chapter:",
        "",
        "| Chapter | Targets | Covered | Ratio | Paragraphs | Words | Time |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        ratio = (len(r.final_covered) / r.target_count) if r.target_count else 0.0
        lines.append(
            f"| {r.chapter} | {r.target_count} | {len(r.final_covered)} "
            f"| {ratio:.0%} | {len(r.paragraphs)} | {r.word_count} | {r.elapsed_s:.1f}s |"
        )
    lines.append("")
    for r in results:
        lines.append(format_chapter_md(r))
    return "\n".join(lines)


# ─── Entrypoint ──────────────────────────────────────────────────────────────


def parse_chapter_spec(spec: str, max_chapter: int) -> list[int]:
    """Parse a chapter spec like "1", "1,3,5", "1-5", "1-5,7,9-11"."""
    if spec.strip().lower() == "all":
        return list(range(1, max_chapter + 1))
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(c for c in out if 1 <= c <= max_chapter)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tsv", type=Path, default=DEFAULT_TSV,
                    help="LLPSI vocabulary TSV (chapter membership source)")
    ap.add_argument("--chapter", default="1",
                    help='Chapters to process: "1", "1,3", "1-5,7", or "all"')
    ap.add_argument("--all", action="store_true",
                    help="Shortcut for --chapter all")
    ap.add_argument("--passes", type=int, default=3,
                    help="Max LLM generation passes per chapter")
    ap.add_argument("--coverage-threshold", type=float, default=0.85,
                    help="Stop passes early when this fraction of targets covered")
    ap.add_argument("--sentences-per-pass", type=int, default=6)
    ap.add_argument("--model", default="sonnet",
                    help="llm_cli model alias (sonnet/haiku/...)")
    ap.add_argument("--report-out", type=Path, default=None,
                    help="Markdown report path (default: research/polyglot-llpsi-coverage-YYYY-MM-DD.md)")
    ap.add_argument("--json-out", type=Path, default=None,
                    help="Machine-readable run dump (paragraphs + coverage)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    raw_entries = load_llpsi(args.tsv)
    raw_by_chapter = index_by_chapter(raw_entries)
    max_chapter = max(raw_by_chapter) if raw_by_chapter else 0
    LOG.info("Loaded %d raw LLPSI entries across chapters 1..%d from %s",
             len(raw_entries), max_chapter, args.tsv)

    spec = "all" if args.all else args.chapter
    chapters = parse_chapter_spec(spec, max_chapter)
    if not chapters:
        LOG.error("No chapters resolved from spec %r", spec)
        return 2

    provider = LatinProvider()
    # Force LatinCy load up-front so we fail fast if it's missing.
    try:
        provider._ensure_latincy()
    except Exception as exc:
        LOG.error("LatinCy unavailable (%s); coverage measurement requires it. "
                  "Install via `pip install -e .[la]` + the LatinCy wheel "
                  "from huggingface.", exc)
        return 3

    # Augment each TSV entry with the LatinCy-isolated lemma as a secondary
    # match key. We keep the TSV row count as-is (LLPSI presents `est`/`sunt`
    # as separate vocab items, so the chapter coverage count should too) and
    # union the bares so context-dependent LatinCy outputs both count.
    canon_entries = augment_match_bares(raw_entries, provider)
    by_chapter = index_by_chapter(canon_entries)
    LOG.info("Augmented → %d targets across %d chapters (mean %.1f match keys/target)",
             len(canon_entries), len(by_chapter),
             sum(len(e.match_bares) for e in canon_entries) / max(1, len(canon_entries)))

    # Resolve report paths up front so we can checkpoint after every chapter.
    cfg = {
        "model": args.model,
        "passes": args.passes,
        "coverage_threshold": args.coverage_threshold,
        "sentences_per_pass": args.sentences_per_pass,
        "chapters": chapters,
        "tsv": str(args.tsv),
    }
    report_path = args.report_out
    if report_path is None:
        DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).date().isoformat()
        report_path = DEFAULT_REPORT_DIR / f"polyglot-llpsi-coverage-{today}.md"

    def _checkpoint(results: list[ChapterResult]) -> None:
        """Rewrite the markdown + JSON dump with whatever's done so far.
        Cheap (KB-scale) and means a 2h run that crashes still leaves a
        usable artifact."""
        if not results:
            return
        report_path.write_text(format_summary_md(results, cfg), encoding="utf-8")
        if args.json_out:
            args.json_out.write_text(json.dumps({
                "config": cfg,
                "results": [
                    {
                        "chapter": r.chapter,
                        "target_count": r.target_count,
                        "covered_count": len(r.final_covered),
                        "paragraphs": r.paragraphs,
                        "covered_per_pass": r.covered_per_pass,
                        "missing": [{"surface": m.surface, "gloss": m.gloss}
                                    for m in r.final_missing],
                        "word_count": r.word_count,
                        "elapsed_s": r.elapsed_s,
                    }
                    for r in results
                ],
            }, indent=2, ensure_ascii=False), encoding="utf-8")

    results: list[ChapterResult] = []
    for ch in chapters:
        targets = by_chapter.get(ch, [])
        # Scaffold: everything from earlier chapters (already part of the
        # learner's confirmed-or-assumed vocabulary).
        scaffold: list[LlpsiEntry] = []
        for earlier in range(1, ch):
            scaffold.extend(by_chapter.get(earlier, []))
        LOG.info("Chapter %d: %d targets, %d scaffold lemmas",
                 ch, len(targets), len(scaffold))
        if not targets:
            continue
        result = run_chapter(
            chapter=ch,
            targets=targets,
            scaffold=scaffold,
            passes=args.passes,
            coverage_threshold=args.coverage_threshold,
            sentences_per_pass=args.sentences_per_pass,
            model=args.model,
            provider=provider,
        )
        results.append(result)
        ratio = (len(result.final_covered) / result.target_count) if result.target_count else 0.0
        print(f"\n=== Chapter {ch}: {len(result.final_covered)}/{result.target_count} "
              f"({ratio:.0%}) in {len(result.paragraphs)} paragraph(s), "
              f"{result.word_count} words, {result.elapsed_s:.1f}s ===\n")
        for i, para in enumerate(result.paragraphs, start=1):
            print(f"--- Paragraph {i} ---")
            print(para.strip())
            print()
        if result.final_missing:
            print(f"Still missing ({len(result.final_missing)}): "
                  + ", ".join(m.surface for m in result.final_missing[:30])
                  + ("..." if len(result.final_missing) > 30 else ""))
        _checkpoint(results)

    LOG.info("Wrote markdown report → %s", report_path)
    if args.json_out:
        LOG.info("Wrote JSON dump → %s", args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
