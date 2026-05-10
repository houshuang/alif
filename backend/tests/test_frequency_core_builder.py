from datetime import datetime

from app.models import Lemma, Sentence, SentenceWord
from scripts.build_frequency_core import (
    CoreCandidate,
    add_source,
    apply_source_agreement_penalty,
    finalize_candidate_confidence,
    load_ranked_file,
    load_hindawi_from_db_corpus,
)


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


def test_source_agreement_penalty_downranks_one_corpus_outlier():
    cand = CoreCandidate(
        key="lemma:1",
        display_form="مَارٌ",
        normalized="مار",
        lemma_id=1,
        score=100.0,
        source_flags={"hindawi": {"rank": 58}},
    )

    apply_source_agreement_penalty(cand)

    assert cand.score == 72.0
    assert cand.source_flags["agreement_penalty"]["strong_sources"] == 1


def test_source_agreement_penalty_keeps_multi_source_or_curriculum_words():
    multi_source = CoreCandidate(
        key="lemma:1",
        display_form="قَالَ",
        normalized="قال",
        lemma_id=1,
        score=100.0,
        source_flags={"hindawi": {"rank": 1}, "news": {"rank": 7}},
    )
    curriculum = CoreCandidate(
        key="lemma:2",
        display_form="مَطار",
        normalized="مطار",
        lemma_id=2,
        score=100.0,
        source_flags={"camel": {"rank": 55}, "db_avp_a1": {"points": 50.0}},
    )

    apply_source_agreement_penalty(multi_source)
    apply_source_agreement_penalty(curriculum)

    assert multi_source.score == 100.0
    assert curriculum.score == 100.0
    assert "agreement_penalty" not in multi_source.source_flags
    assert "agreement_penalty" not in curriculum.source_flags


def test_load_ranked_file_accepts_samer_style_columns(tmp_path):
    path = tmp_path / "samer.tsv"
    path.write_text(
        "Occurrences\tlemma#pos\tGloss\n"
        "335409\tفِي#prep\tin\n"
        "181283\tأَنَّ#conj_sub\tthat\n",
        encoding="utf-8",
    )

    rows = load_ranked_file(path)

    assert rows == [
        ("فِي#prep", 1, 335409, None),
        ("أَنَّ#conj_sub", 2, 181283, None),
    ]


def test_load_hindawi_from_db_corpus_rolls_up_canonical_counts(db_session):
    gated_at = datetime(2026, 1, 1)
    canonical = Lemma(
        lemma_id=1,
        lemma_ar="ذَهَبَ",
        lemma_ar_bare="ذهب",
        gloss_en="to go",
        pos="verb",
        gates_completed_at=gated_at,
    )
    variant = Lemma(
        lemma_id=2,
        lemma_ar="ذَهَبُوا",
        lemma_ar_bare="ذهبوا",
        gloss_en="they went",
        pos="verb",
        canonical_lemma_id=1,
        gates_completed_at=gated_at,
    )
    other = Lemma(
        lemma_id=3,
        lemma_ar="بَيْت",
        lemma_ar_bare="بيت",
        gloss_en="house",
        pos="noun",
        gates_completed_at=gated_at,
    )
    db_session.add_all([canonical, variant, other])
    db_session.flush()

    corpus_sentence = Sentence(arabic_text="ذهبوا الى بيت", source="corpus")
    llm_sentence = Sentence(arabic_text="ذهب", source="llm")
    db_session.add_all([corpus_sentence, llm_sentence])
    db_session.flush()
    db_session.add_all([
        SentenceWord(sentence_id=corpus_sentence.id, position=1, surface_form="ذهبوا", lemma_id=2),
        SentenceWord(sentence_id=corpus_sentence.id, position=2, surface_form="بيت", lemma_id=3),
        SentenceWord(sentence_id=corpus_sentence.id, position=3, surface_form="ذهب", lemma_id=1),
        SentenceWord(sentence_id=llm_sentence.id, position=1, surface_form="ذهب", lemma_id=1),
    ])
    db_session.commit()

    rows = load_hindawi_from_db_corpus(
        db_session,
        {1: canonical, 2: variant, 3: other},
    )

    assert rows == [(1, 1, 2), (3, 2, 1)]
