"""Before/after measurement for the at-risk scaffold bias (sh/generation-at-risk-scaffold).

Generates sentences for a fixed target set with the bias OFF then ON, against a
prod DB copy, and compares scaffold composition: at-risk content words per
sentence and the share of sentences whose scaffold is entirely mature/known.
Pure read of the DB + LLM calls (no writes).

Usage:
    python3 scripts/measure_at_risk_scaffold.py --db /tmp/claude/alif_prod_meas.db --targets 15
"""
from __future__ import annotations
import argparse, json, random
from collections import Counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Lemma, UserLemmaKnowledge
from app.services.sentence_generator import (
    get_content_word_counts, sample_known_words_weighted, build_at_risk_boost_map,
    get_avoid_words, KNOWN_SAMPLE_SIZE,
)
from app.services.sentence_validator import build_lemma_lookup, strip_diacritics, tokenize
from app.services.llm import generate_sentences_batch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--targets", type=int, default=15)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)

    engine = create_engine(f"sqlite:///{args.db}")
    db = sessionmaker(bind=engine)()

    active = (db.query(Lemma).join(UserLemmaKnowledge)
              .filter(UserLemmaKnowledge.knowledge_state.in_(["known","learning","lapsed","acquiring"]))
              .all())
    known_words = [{"arabic": l.lemma_ar, "english": l.gloss_en or "", "lemma_id": l.lemma_id, "pos": l.pos or ""} for l in active]
    lemma_lookup = build_lemma_lookup(active)
    content_word_counts = get_content_word_counts(db)
    at_risk_boost = build_at_risk_boost_map(db)
    # stability per lemma for classification
    stab = {}
    for lid, cj in db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.fsrs_card_json):
        try:
            c = json.loads(cj) if isinstance(cj, str) else cj
            stab[lid] = c.get("stability") if isinstance(c, dict) else None
        except Exception:
            stab[lid] = None

    print(f"active lemmas={len(known_words)}  at_risk_pool={len(at_risk_boost)}")

    # target set: due-ish KNOWN words that actually have material (pick from known pool)
    known_ids = [l.lemma_id for l in active if l.gloss_en]
    targets = random.sample(known_ids, args.targets)
    tgt = {l.lemma_id: l for l in active}

    def classify(arabic, target_bare):
        """Return (n_content, n_at_risk) for scaffold (non-target) content words."""
        n_content = n_atrisk = 0
        for tok in tokenize(arabic):
            bare = strip_diacritics(tok)
            if bare == target_bare:
                continue
            lid = lemma_lookup.get(bare)
            if lid is None:
                continue  # function word / unmapped
            n_content += 1
            if at_risk_boost.get(lid, 1.0) > 1.0:
                n_atrisk += 1
        return n_content, n_atrisk

    def run(label, boost):
        sentences = 0; atrisk_total = 0; content_total = 0; all_mature = 0
        for lid in targets:
            lem = tgt[lid]
            sample = sample_known_words_weighted(
                known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
                target_lemma_id=lid, at_risk_boost=boost)
            avoid = get_avoid_words(content_word_counts, known_words)
            try:
                res = generate_sentences_batch(
                    target_word=lem.lemma_ar, target_translation=lem.gloss_en or "",
                    known_words=sample, count=args.count, difficulty_hint="simple",
                    avoid_words=avoid, max_words=10, rerank=False,
                    model_override="claude_sonnet")
            except Exception as e:
                print(f"  gen fail {lem.lemma_ar}: {e}")
                continue
            tb = lem.lemma_ar_bare or strip_diacritics(lem.lemma_ar)
            for s in res:
                nc, na = classify(s.arabic, tb)
                if nc == 0:
                    continue
                sentences += 1; content_total += nc; atrisk_total += na
                if na == 0:
                    all_mature += 1
        print(f"\n[{label}]  sentences={sentences}")
        if sentences:
            print(f"  at-risk scaffold words / sentence: {atrisk_total/sentences:.2f}")
            print(f"  share of scaffold content words at-risk: {atrisk_total/max(1,content_total):.1%}")
            print(f"  sentences with ZERO at-risk scaffold (all-mature): {all_mature}/{sentences} ({all_mature/sentences:.0%})")
        return sentences, atrisk_total, all_mature

    print("\n=== BIAS OFF ===")
    run("OFF", None)
    print("\n=== BIAS ON ===")
    run("ON", at_risk_boost)
    db.close()


if __name__ == "__main__":
    main()
