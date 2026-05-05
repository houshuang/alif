from app.models import Lemma
from scripts.build_frequency_core import CoreCandidate, add_source, finalize_candidate_confidence


def test_add_source_uses_best_source_rank_once_per_lemma():
    lemma = Lemma(lemma_id=1, lemma_ar="كِتَاب", lemma_ar_bare="كتاب", gloss_en="book", pos="noun")
    candidates: dict[str, CoreCandidate] = {}
    lookup = {"كتاب": 1, "الكتاب": 1}

    add_source(
        candidates,
        form="كتاب",
        source="camel",
        rank=100,
        count=10,
        lemma_lookup=lookup,
        lemmas_by_id={1: lemma},
    )
    first_score = candidates["lemma:1"].score
    add_source(
        candidates,
        form="الكتاب",
        source="camel",
        rank=150,
        count=5,
        lemma_lookup=lookup,
        lemmas_by_id={1: lemma},
    )

    cand = candidates["lemma:1"]
    assert cand.score == first_score
    assert cand.camel_count == 15
    assert cand.source_flags["camel"]["rank"] == 100


def test_confidence_high_requires_two_broad_sources():
    cand = CoreCandidate(key="lemma:1", display_form="كتاب", normalized="كتاب", lemma_id=1)
    cand.source_flags = {"camel": {}, "artenten": {}}

    finalize_candidate_confidence(cand)

    assert cand.confidence_tier == "high"
    assert cand.broad_source_count == 2


def test_kelly_duplicate_keeps_cefr_from_best_rank():
    lemma = Lemma(lemma_id=1, lemma_ar="كِتَاب", lemma_ar_bare="كتاب", gloss_en="book", pos="noun")
    candidates: dict[str, CoreCandidate] = {}
    lookup = {"كتاب": 1, "الكتاب": 1}

    add_source(
        candidates,
        form="كتاب",
        source="kelly",
        rank=100,
        cefr="A1",
        lemma_lookup=lookup,
        lemmas_by_id={1: lemma},
    )
    add_source(
        candidates,
        form="الكتاب",
        source="kelly",
        rank=2000,
        cefr="B1",
        lemma_lookup=lookup,
        lemmas_by_id={1: lemma},
    )

    cand = candidates["lemma:1"]
    assert cand.kelly_rank == 100
    assert cand.kelly_cefr == "A1"
    assert cand.source_flags["kelly"]["cefr"] == "A1"
