"""Rare words in the generation prompt and in generated review sentences.

Read-only analysis for research/analysis-2026-09-18-sentence-rarity.md. Run it
against a scratch COPY of a production backup (the app's engine sets WAL
pragmas on connect, so never point it at the backup itself):

    cp ~/alif-backups/alif_YYYYMMDD_HHMMSS.db /tmp/copy.db
    cd backend && DATABASE_URL=sqlite:////tmp/copy.db ALIF_SKIP_MIGRATIONS=1 \
      .venv/bin/python ../research/sentence-rarity-2026-09-18/analyze_rarity.py \
      > ../research/sentence-rarity-2026-09-18/results.json

Add --repair-ranks to overlay the CAMeL rank that assign_frequency_rank()
gives every lemma stored without frequency_rank (the rank the quality gates
would have assigned had _CAMEL_CACHE pointed at backend/data), without writing.

Rare = effective frequency rank > 5,000 or unranked (frequency_lanes). Content
words are classified by the RESOLVED lemma (function-word check on the lemma
bare plus word_category), as CLAUDE.md requires. No LLM calls.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from types import SimpleNamespace
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, ReviewLog, Sentence, SentenceWord, UserLemmaKnowledge  # noqa: E402
from app.services.frequency_lanes import UNKNOWN_FREQUENCY_RANK, effective_frequency_ranks  # noqa: E402
from app.services.sentence_eligibility import reviewable_sentence_clauses  # noqa: E402
from app.services.sentence_generator import (  # noqa: E402
    KNOWN_SAMPLE_SIZE,
    build_at_risk_boost_map,
    get_content_word_counts,
    sample_known_words_weighted,
)
from app.services.sentence_validator import is_function_word_lemma  # noqa: E402

RARE_RANK = 5000
ACTIVE_STATES = ("known", "learning", "lapsed", "acquiring")
INERT_CATEGORIES = {"proper_name", "onomatopoeia"}
SAMPLE_DRAWS = 200
SPEC_SAMPLE = Path(__file__).resolve().parents[1] / "spec-2026-09-18" / "sentence-sample.json"


def band(rank: int) -> str:
    if rank >= UNKNOWN_FREQUENCY_RANK:
        return "unranked"
    for limit, label in ((1000, "<=1k"), (2000, "1-2k"), (5000, "2-5k"), (20000, "5-20k")):
        if rank <= limit:
            return label
    return ">20k"


def share(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def main() -> None:
    repair_ranks = "--repair-ranks" in sys.argv[1:]
    db = SessionLocal()
    snapshot_at = db.query(ReviewLog.reviewed_at).order_by(ReviewLog.reviewed_at.desc()).first()[0]
    if snapshot_at.tzinfo is None:
        snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)

    lemmas = {l.lemma_id: l for l in db.query(Lemma).all()}

    def canonical(lid: int) -> int:
        seen = set()
        while lid in lemmas and lemmas[lid].canonical_lemma_id and lid not in seen:
            seen.add(lid)
            lid = lemmas[lid].canonical_lemma_id
        return lid

    def is_content(lid: int) -> bool:
        lem = lemmas.get(lid)
        if lem is None:
            return False
        if lem.word_category in INERT_CATEGORIES:
            return False
        return not is_function_word_lemma(lem.lemma_ar_bare, lem.function_word_override)

    ranks = effective_frequency_ranks(db, lemmas.keys())
    repaired = 0
    if repair_ranks:
        from app.services.lemma_quality import assign_frequency_rank

        for lid, lem in lemmas.items():
            if lem.frequency_rank is not None:
                continue
            probe = SimpleNamespace(lemma_ar_bare=lem.lemma_ar_bare, frequency_rank=None)
            if assign_frequency_rank(probe):
                repaired += 1
                ranks[lid] = min(ranks.get(lid, UNKNOWN_FREQUENCY_RANK), probe.frequency_rank)

    def rank_of(lid: int) -> int:
        return ranks.get(canonical(lid), UNKNOWN_FREQUENCY_RANK)

    def is_rare(lid: int) -> bool:
        return rank_of(lid) > RARE_RANK

    # ── 1. The vocabulary pool the generator samples from ──────────────────
    ulks = {u.lemma_id: u for u in db.query(UserLemmaKnowledge).all()}
    pool_ids = [lid for lid, u in ulks.items() if u.knowledge_state in ACTIVE_STATES and lid in lemmas]
    content_pool = [lid for lid in pool_ids if is_content(lid)]
    pool_bands = Counter(band(rank_of(lid)) for lid in content_pool)
    rare_pool = [lid for lid in content_pool if is_rare(lid)]
    rare_by_source = Counter((ulks[lid].source or "none") for lid in rare_pool)
    common_by_source = Counter((ulks[lid].source or "none") for lid in content_pool if not is_rare(lid))

    counts = get_content_word_counts(db)
    rare_counts = [counts.get(lid, 0) for lid in rare_pool]
    common_counts = [counts.get(lid, 0) for lid in content_pool if not is_rare(lid)]

    pool = {
        "active_lemmas": len(pool_ids),
        "content_lemmas": len(content_pool),
        "rank_bands": dict(pool_bands),
        "rare_share": share(len(rare_pool), len(content_pool)),
        "rare_by_ulk_source": dict(rare_by_source.most_common()),
        "common_by_ulk_source": dict(common_by_source.most_common()),
        "sentences_as_scaffold": {
            "rare_median": statistics.median(rare_counts) if rare_counts else 0,
            "common_median": statistics.median(common_counts) if common_counts else 0,
            "rare_zero_share": share(sum(c == 0 for c in rare_counts), len(rare_counts)),
            "common_zero_share": share(sum(c == 0 for c in common_counts), len(common_counts)),
        },
    }

    # ── 2. What the current sampler hands the model ────────────────────────
    known_words = [
        {"arabic": lemmas[lid].lemma_ar, "english": lemmas[lid].gloss_en or "",
         "lemma_id": lid, "pos": lemmas[lid].pos or ""}
        for lid in pool_ids
    ]
    boost = build_at_risk_boost_map(db)

    def sampler_stats(label: str, draw) -> dict:
        total_rare = total_content = head_rare = head_content = 0
        for i in range(SAMPLE_DRAWS):
            random.seed(i)
            sample = draw()
            content = [w["lemma_id"] for w in sample if is_content(w["lemma_id"])]
            total_content += len(content)
            total_rare += sum(is_rare(lid) for lid in content)
            head = content[:100]
            head_content += len(head)
            head_rare += sum(is_rare(lid) for lid in head)
        return {
            "label": label,
            "rare_share": share(total_rare, total_content),
            "rare_share_first_100": share(head_rare, head_content),
        }

    sampler = [
        sampler_stats("current (inverse count x at-risk, sorted by weight)", lambda: sample_known_words_weighted(
            known_words, counts, KNOWN_SAMPLE_SIZE, at_risk_boost=boost)),
        sampler_stats("inverse count only", lambda: sample_known_words_weighted(
            known_words, counts, KNOWN_SAMPLE_SIZE)),
        sampler_stats("uniform random", lambda: random.sample(known_words, KNOWN_SAMPLE_SIZE)),
    ]

    # ── 3. Active reviewable generated sentences ───────────────────────────
    reviewable = (
        db.query(Sentence)
        .filter(Sentence.is_active == True, reviewable_sentence_clauses())  # noqa: E712
        .all()
    )
    words_by_sentence: dict[int, list[SentenceWord]] = defaultdict(list)
    ids = [s.id for s in reviewable]
    for chunk_start in range(0, len(ids), 900):
        chunk = ids[chunk_start:chunk_start + 900]
        for sw in db.query(SentenceWord).filter(SentenceWord.sentence_id.in_(chunk)).all():
            words_by_sentence[sw.sentence_id].append(sw)

    def rare_nontarget(sent: Sentence) -> tuple[int, int]:
        target = canonical(sent.target_lemma_id) if sent.target_lemma_id else None
        content = rare = 0
        seen = set()
        for sw in words_by_sentence[sent.id]:
            if sw.lemma_id is None or sw.is_target_word:
                continue
            lid = canonical(sw.lemma_id)
            if lid == target or lid in seen or not is_content(lid):
                continue
            seen.add(lid)
            content += 1
            rare += is_rare(lid)
        return rare, content

    per_sentence = {s.id: rare_nontarget(s) for s in reviewable}
    llm_sentences = [s for s in reviewable if s.source == "llm"]

    def distribution(sentences) -> dict:
        buckets = Counter(min(per_sentence[s.id][0], 3) for s in sentences)
        rare = sum(per_sentence[s.id][0] for s in sentences)
        content = sum(per_sentence[s.id][1] for s in sentences)
        return {
            "sentences": len(sentences),
            "rare_nontarget_0": buckets[0], "rare_nontarget_1": buckets[1],
            "rare_nontarget_2": buckets[2], "rare_nontarget_3plus": buckets[3],
            "mean_rare_nontarget": round(rare / len(sentences), 3) if sentences else 0,
            "rare_share_of_scaffold": share(rare, content),
        }

    by_month = defaultdict(list)
    for s in llm_sentences:
        by_month[s.created_at.strftime("%Y-%m") if s.created_at else "unknown"].append(s)

    target_rare = sum(1 for s in llm_sentences if s.target_lemma_id and is_rare(s.target_lemma_id))

    # ── 4. B2 pre-gate simulation and coverage impact ──────────────────────
    def due_now(u: UserLemmaKnowledge) -> bool:
        if u.knowledge_state == "acquiring":
            due = u.acquisition_next_due
        elif u.knowledge_state in ("known", "learning", "lapsed"):
            card = u.fsrs_card_json if isinstance(u.fsrs_card_json, dict) else json.loads(u.fsrs_card_json or "{}")
            raw = card.get("due")
            due = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
        else:
            return False
        if due is None:
            return False
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return due <= snapshot_at + timedelta(days=7)

    demand = {lid for lid in pool_ids if is_content(lid) and due_now(ulks[lid])}
    covering: dict[int, set[int]] = defaultdict(set)
    for s in reviewable:
        for sw in words_by_sentence[s.id]:
            if sw.lemma_id is not None:
                covering[canonical(sw.lemma_id)].add(s.id)

    gate = {}
    for threshold in (0, 1, 2):
        rejected = {s.id for s in llm_sentences if per_sentence[s.id][0] > threshold}
        lost = [lid for lid in demand if covering[lid] and not (covering[lid] - rejected)]
        gate[f"reject_if_more_than_{threshold}"] = {
            "llm_rejected": len(rejected),
            "llm_rejected_share": share(len(rejected), len(llm_sentences)),
            "due_within_7d_lemmas": len(demand),
            "due_lemmas_left_without_any_sentence": len(lost),
            "of_which_rare": sum(is_rare(lid) for lid in lost),
        }

    # ── 5. The spec's 60 rated sentences ───────────────────────────────────
    rated = json.loads(SPEC_SAMPLE.read_text())["sample"]
    by_id = {s.id: s for s in db.query(Sentence).filter(Sentence.id.in_([r["sentence_id"] for r in rated])).all()}
    for sid in by_id:
        if sid not in words_by_sentence:
            words_by_sentence[sid] = db.query(SentenceWord).filter(SentenceWord.sentence_id == sid).all()
    rated_rows = []
    for r in rated:
        sent = by_id.get(r["sentence_id"])
        if sent is None:
            continue
        rare, content = rare_nontarget(sent)
        rated_rows.append({"rating": r["rating"], "rare_nontarget": rare, "content_nontarget": content,
                           "source": sent.source})
    separation = {}
    for rating in ("N", "O", "S"):
        rows = [row for row in rated_rows if row["rating"] == rating]
        separation[rating] = {
            "n": len(rows),
            "mean_rare_nontarget": round(statistics.mean(row["rare_nontarget"] for row in rows), 2) if rows else None,
            "share_rejected_at_gt1": share(sum(row["rare_nontarget"] > 1 for row in rows), len(rows)),
            "share_rejected_at_gt0": share(sum(row["rare_nontarget"] > 0 for row in rows), len(rows)),
        }

    # ── 6. Generation-time trend, including retired sentences ──────────────
    # Active-by-month is confounded by retirement; this counts every generated
    # sentence ever stored, with today's ranks.
    all_llm = db.query(Sentence).filter(Sentence.source == "llm").all()
    missing = [s.id for s in all_llm if s.id not in words_by_sentence]
    for chunk_start in range(0, len(missing), 900):
        chunk = missing[chunk_start:chunk_start + 900]
        for sw in db.query(SentenceWord).filter(SentenceWord.sentence_id.in_(chunk)).all():
            words_by_sentence[sw.sentence_id].append(sw)
    generated_by_month = defaultdict(list)
    for s in all_llm:
        per_sentence.setdefault(s.id, rare_nontarget(s))
        generated_by_month[s.created_at.strftime("%Y-%m") if s.created_at else "unknown"].append(s)

    # ── 7. When rare words entered the active pool, and from where ─────────
    def entered(u: UserLemmaKnowledge):
        return u.introduced_at or u.entered_acquiring_at or u.acquisition_started_at

    rare_entry = Counter(
        (entered(ulks[lid]).strftime("%Y-%m") if entered(ulks[lid]) else "unknown", ulks[lid].source or "none")
        for lid in rare_pool
    )
    rare_entry_by_month = defaultdict(dict)
    for (month, source), n in sorted(rare_entry.items()):
        rare_entry_by_month[month][source] = n

    # ── 8. Which rare words carry the recent scaffold ──────────────────────
    recent = [s for s in all_llm if s.created_at and s.created_at >= datetime(2026, 7, 1)]
    rare_scaffold = Counter()
    for s in recent:
        target = canonical(s.target_lemma_id) if s.target_lemma_id else None
        for lid in {canonical(sw.lemma_id) for sw in words_by_sentence[s.id]
                    if sw.lemma_id is not None and not sw.is_target_word}:
            if lid != target and is_content(lid) and is_rare(lid):
                rare_scaffold[lid] += 1
    top_rare_scaffold = [
        {"lemma_id": lid, "arabic": lemmas[lid].lemma_ar, "gloss": lemmas[lid].gloss_en,
         "rank_band": band(rank_of(lid)), "ulk_source": (ulks.get(lid).source if ulks.get(lid) else None),
         "state": (ulks.get(lid).knowledge_state if ulks.get(lid) else None),
         "sentences_since_july": n}
        for lid, n in rare_scaffold.most_common(40)
    ]
    rare_scaffold_by_source = Counter()
    for lid, n in rare_scaffold.items():
        rare_scaffold_by_source[(ulks.get(lid).source if ulks.get(lid) else None) or "none"] += n

    json.dump({
        "snapshot_at": snapshot_at.isoformat(),
        "rare_rank_threshold": RARE_RANK,
        "ranks": "repaired" if repair_ranks else "as stored",
        "lemmas_given_a_camel_rank_by_repair": repaired,
        "pool": pool,
        "sampler": sampler,
        "active_reviewable": {
            "all_sources": distribution(reviewable),
            "llm": distribution(llm_sentences),
            "llm_by_created_month": {m: distribution(v) for m, v in sorted(by_month.items())},
            "llm_rare_target_share": share(target_rare, len(llm_sentences)),
            "by_source": {src: distribution([s for s in reviewable if s.source == src])
                          for src in sorted({s.source or "none" for s in reviewable})},
        },
        "rarity_pre_gate": gate,
        "rated_sample": separation,
        "generated_by_month_all": {m: distribution(v) for m, v in sorted(generated_by_month.items())},
        "rare_pool_entry_by_month_and_source": dict(rare_entry_by_month),
        "rare_scaffold_since_july_by_ulk_source": dict(rare_scaffold_by_source.most_common()),
        "top_rare_scaffold_since_july": top_rare_scaffold,
    }, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
