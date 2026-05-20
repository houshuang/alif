"""Tests for the picker + minimal session builder (PR #3).

Covers the three hard invariants the picker honors (reviewability gate at
``mappings_verified_at`` + ``is_active``, canonical resolution at entry,
function-word/proper-name scaffold skip) and the three-tier source preference
(page-first all-known > harvested by comprehensibility > None).

Constructs Sentence/SentenceWord rows directly rather than going through the
sentence_harvest pipeline — the harvest path is exercised in
test_sentence_harvest.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    Lemma,
    Page,
    Sentence,
    SentenceWord,
    Story,
    UserLemmaKnowledge,
)
from app.services.fsrs_service import create_new_card
from app.services.sentence_selector import (
    build_session,
    pick_sentence_for_lemma,
)


def _seed_lemma(
    db,
    *,
    form: str,
    bare: str | None = None,
    language_code: str = "el",
    canonical: int | None = None,
    word_category: str | None = None,
) -> Lemma:
    lemma = Lemma(
        language_code=language_code,
        lemma_form=form,
        lemma_bare=bare if bare is not None else form,
        source="test",
        canonical_lemma_id=canonical,
        word_category=word_category,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _seed_sentence(
    db,
    *,
    lemma_surfaces: list[tuple[int, str]],
    language_code: str = "el",
    text: str | None = None,
    verified: bool = True,
    is_active: bool = True,
    source: str = "textbook",
    page_id: int | None = None,
) -> Sentence:
    sentence = Sentence(
        language_code=language_code,
        text=text or " ".join(s for _, s in lemma_surfaces),
        source=source,
        page_id=page_id,
        sentence_index_in_page=0 if page_id else None,
        is_active=is_active,
        mappings_verified_at=datetime.now(timezone.utc) if verified else None,
    )
    db.add(sentence)
    db.flush()
    for i, (lemma_id, surface) in enumerate(lemma_surfaces):
        db.add(SentenceWord(
            sentence_id=sentence.id,
            position=i,
            surface_form=surface,
            lemma_id=lemma_id,
        ))
    db.flush()
    return sentence


def _seed_page(db, *, story_id: int, page_number: int = 1) -> Page:
    page = Page(
        story_id=story_id,
        page_number=page_number,
        body_src="dummy",
        processed_at=datetime.now(timezone.utc),
        mappings_verified_at=datetime.now(timezone.utc),
    )
    db.add(page)
    db.flush()
    return page


def _seed_story(db, *, language_code: str = "el") -> Story:
    story = Story(
        language_code=language_code,
        title="Test",
        source="paste",
    )
    db.add(story)
    db.flush()
    return story


def _seed_known(db, lemma_id: int, state: str = "known") -> UserLemmaKnowledge:
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        fsrs_card_json=create_new_card() if state in ("known", "learning", "lapsed") else None,
    )
    db.add(ulk)
    db.flush()
    return ulk


def _seed_acquiring(db, lemma_id: int, *, box: int = 1, due_offset_s: int = -60) -> UserLemmaKnowledge:
    now = datetime.now(timezone.utc)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="acquiring",
        acquisition_box=box,
        acquisition_next_due=now + timedelta(seconds=due_offset_s),
        acquisition_started_at=now,
    )
    db.add(ulk)
    db.flush()
    return ulk


# ─── Picker tests ─────────────────────────────────────────────────────────


def test_returns_none_when_no_candidate(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        db.commit()
        result = pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el")
        assert result is None


def test_picks_only_candidate_when_one_exists(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        sent = _seed_sentence(
            db,
            lemma_surfaces=[(target.lemma_id, "λόγος")],
            text="λόγος",
        )
        db.commit()
        result = pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el")
        assert result is not None
        assert result.sentence_id == sent.id
        assert result.target_lemma_id == target.lemma_id


def test_skips_unverified_sentences(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        _seed_sentence(
            db,
            lemma_surfaces=[(target.lemma_id, "λόγος")],
            verified=False,
        )
        db.commit()
        assert pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el") is None


def test_skips_inactive_sentences(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        _seed_sentence(
            db,
            lemma_surfaces=[(target.lemma_id, "λόγος")],
            is_active=False,
        )
        db.commit()
        assert pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el") is None


def test_exclude_sentence_ids_skipped(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        a = _seed_sentence(db, lemma_surfaces=[(target.lemma_id, "λόγος Α")], text="alpha")
        b = _seed_sentence(db, lemma_surfaces=[(target.lemma_id, "λόγος Β")], text="beta")
        db.commit()

        result = pick_sentence_for_lemma(
            db, lemma_id=target.lemma_id, language_code="el",
            exclude_sentence_ids={a.id},
        )
        assert result is not None
        assert result.sentence_id == b.id


def test_page_first_all_known_ranks_above_other_sources(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        scaffold = _seed_lemma(db, form="ο")
        _seed_known(db, scaffold.lemma_id, state="known")

        story = _seed_story(db)
        page = _seed_page(db, story_id=story.id)

        # Non-page LLM sentence with same all-known scaffold
        llm_sent = _seed_sentence(
            db,
            lemma_surfaces=[(scaffold.lemma_id, "ο"), (target.lemma_id, "λόγος")],
            text="ο λόγος (llm)",
            source="llm",
        )
        # Page sentence with same all-known scaffold — should win
        page_sent = _seed_sentence(
            db,
            lemma_surfaces=[(scaffold.lemma_id, "ο"), (target.lemma_id, "λόγος")],
            text="ο λόγος (page)",
            source="textbook",
            page_id=page.id,
        )
        db.commit()

        result = pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el")
        assert result is not None
        assert result.sentence_id == page_sent.id
        assert result.selection_reason == "page_first_all_known"


def test_page_first_unknown_scaffold_falls_back_to_other_source(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        # Unknown scaffold word — no ULK row
        unknown_scaffold = _seed_lemma(db, form="ξένο")

        story = _seed_story(db)
        page = _seed_page(db, story_id=story.id)

        # Page sentence has unknown scaffold → page_first NOT triggered
        page_sent = _seed_sentence(
            db,
            lemma_surfaces=[(unknown_scaffold.lemma_id, "ξένο"), (target.lemma_id, "λόγος")],
            text="ξένο λόγος",
            source="textbook",
            page_id=page.id,
        )
        # LLM sentence has no scaffold → comprehensibility=1.0, no page-first bonus.
        # Both should score; the LLM (no unknown scaffold) should win on score.
        llm_sent = _seed_sentence(
            db,
            lemma_surfaces=[(target.lemma_id, "λόγος")],
            text="λόγος (llm)",
            source="llm",
        )
        db.commit()

        result = pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el")
        assert result is not None
        # Both are eligible — LLM (1.0 comprehensibility, source bonus 1.0) =
        # base 0.3+0.7*1.0=1.0 * 1.0 = 1.0
        # Page (0.0 comprehensibility, source bonus 1.4) =
        # base 0.3+0=0.3 * 1.4 = 0.42, no page_first_bonus
        # LLM wins.
        assert result.sentence_id == llm_sent.id
        # Page row still exists in DB (storage retained for future unlock)
        assert db.query(Sentence).filter(Sentence.id == page_sent.id).first() is not None


def test_function_word_lemmas_dont_count_in_scaffold(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        # Greek function word "και" lives in FUNCTION_WORD_SETS['el']
        func_word = _seed_lemma(db, form="και", bare="και", word_category="function_word")
        # No ULK for func_word — but it shouldn't count anyway
        _seed_sentence(
            db,
            lemma_surfaces=[(func_word.lemma_id, "και"), (target.lemma_id, "λόγος")],
            text="και λόγος",
        )
        db.commit()
        result = pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el")
        assert result is not None
        # Scaffold should be empty (only function word + target) → comprehensibility 1.0
        # selection_reason should be all_scaffold_known (since no scaffold to be unknown)
        assert result.selection_reason in ("all_scaffold_known", "page_first_all_known")


def test_proper_name_lemmas_dont_count_in_scaffold(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        proper = _seed_lemma(db, form="Αθήνα", word_category="proper_name")
        # No ULK for proper name
        _seed_sentence(
            db,
            lemma_surfaces=[(proper.lemma_id, "Αθήνα"), (target.lemma_id, "λόγος")],
            text="Αθήνα λόγος",
        )
        db.commit()
        result = pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el")
        assert result is not None
        # Proper name excluded from scaffold counting → effectively single-word sentence
        assert result.selection_reason in ("all_scaffold_known", "page_first_all_known")


def test_canonical_resolution_at_entry(tmp_db):
    """Variant lemma in → canonical lemma's sentence out (Hard Invariant #9)."""
    with tmp_db() as db:
        canonical = _seed_lemma(db, form="πατήρ")
        variant = _seed_lemma(db, form="πατέρας", canonical=canonical.lemma_id)
        # Sentence only references canonical_id (per sentence_harvest invariant)
        sent = _seed_sentence(
            db,
            lemma_surfaces=[(canonical.lemma_id, "πατήρ")],
            text="πατήρ",
        )
        db.commit()
        # Caller passes the variant id — picker must resolve to canonical first
        result = pick_sentence_for_lemma(db, lemma_id=variant.lemma_id, language_code="el")
        assert result is not None
        assert result.sentence_id == sent.id
        assert result.target_lemma_id == canonical.lemma_id


def test_payload_marks_target_word(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        scaffold = _seed_lemma(db, form="ο", word_category="function_word")
        _seed_sentence(
            db,
            lemma_surfaces=[(scaffold.lemma_id, "ο"), (target.lemma_id, "λόγος")],
            text="ο λόγος",
        )
        db.commit()
        result = pick_sentence_for_lemma(db, lemma_id=target.lemma_id, language_code="el")
        assert result is not None
        targets = [w for w in result.words if w.is_target]
        assert len(targets) == 1
        assert targets[0].lemma_id == target.lemma_id


# ─── Session builder tests ───────────────────────────────────────────────


def test_session_empty_when_nothing_due(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        ulk = _seed_known(db, target.lemma_id, state="known")
        # Push the FSRS card's due time into the future — Card() defaults to
        # due=now, which would otherwise count as due. Reassign a fresh dict
        # so SQLAlchemy detects the change on the JSON column.
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        ulk.fsrs_card_json = {**(ulk.fsrs_card_json or {}), "due": future}
        _seed_sentence(db, lemma_surfaces=[(target.lemma_id, "λόγος")])
        db.commit()
        bundle = build_session(db, language_code="el", limit=10)
        assert bundle.sentences == []
        assert bundle.intro_cards == []


def test_session_picks_one_sentence_per_due_lemma(tmp_db):
    with tmp_db() as db:
        a = _seed_lemma(db, form="λόγος")
        b = _seed_lemma(db, form="κόσμος")
        _seed_acquiring(db, a.lemma_id, box=1)
        _seed_acquiring(db, b.lemma_id, box=1)
        sa = _seed_sentence(db, lemma_surfaces=[(a.lemma_id, "λόγος")], text="A")
        sb = _seed_sentence(db, lemma_surfaces=[(b.lemma_id, "κόσμος")], text="B")
        db.commit()
        bundle = build_session(db, language_code="el", limit=10)
        assert len(bundle.sentences) == 2
        ids = {s.sentence_id for s in bundle.sentences}
        assert ids == {sa.id, sb.id}


def test_session_skips_lemmas_without_eligible_sentences(tmp_db):
    with tmp_db() as db:
        a = _seed_lemma(db, form="λόγος")
        b = _seed_lemma(db, form="κόσμος")  # no sentence → skipped
        _seed_acquiring(db, a.lemma_id)
        _seed_acquiring(db, b.lemma_id)
        sa = _seed_sentence(db, lemma_surfaces=[(a.lemma_id, "λόγος")], text="A")
        db.commit()
        bundle = build_session(db, language_code="el", limit=10)
        assert len(bundle.sentences) == 1
        assert bundle.sentences[0].sentence_id == sa.id


def test_session_dedupes_sentences(tmp_db):
    """If one sentence covers two due lemmas, it's used once."""
    with tmp_db() as db:
        a = _seed_lemma(db, form="λόγος")
        b = _seed_lemma(db, form="κόσμος")
        _seed_acquiring(db, a.lemma_id, box=1)
        _seed_acquiring(db, b.lemma_id, box=1)
        shared = _seed_sentence(
            db,
            lemma_surfaces=[(a.lemma_id, "λόγος"), (b.lemma_id, "κόσμος")],
            text="λόγος κόσμος",
        )
        db.commit()
        bundle = build_session(db, language_code="el", limit=10)
        # First due lemma gets the shared sentence; second is skipped because
        # the only candidate is now excluded.
        assert len(bundle.sentences) == 1
        assert bundle.sentences[0].sentence_id == shared.id


def test_session_respects_limit(tmp_db):
    with tmp_db() as db:
        for i in range(5):
            lemma = _seed_lemma(db, form=f"λ{i}", bare=f"λ{i}")
            _seed_acquiring(db, lemma.lemma_id, box=1, due_offset_s=-60 - i)
            _seed_sentence(db, lemma_surfaces=[(lemma.lemma_id, f"λ{i}")], text=f"S{i}")
        db.commit()
        bundle = build_session(db, language_code="el", limit=3)
        assert len(bundle.sentences) == 3


# ─── HTTP endpoint smoke tests ──────────────────────────────────────────


def _http_client(tmp_db):
    """Return TestClient wired to the per-test DB."""
    def _get_db():
        db = tmp_db()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


def test_endpoint_next_sentence_returns_payload(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        sent = _seed_sentence(db, lemma_surfaces=[(target.lemma_id, "λόγος")])
        db.commit()
        lemma_id = target.lemma_id
        sentence_id = sent.id

    client = _http_client(tmp_db)
    try:
        resp = client.get("/api/reviews/next-sentence", params={
            "lemma_id": lemma_id,
            "language_code": "el",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body is not None
        assert body["sentence_id"] == sentence_id
        assert body["target_lemma_id"] == lemma_id
    finally:
        app.dependency_overrides.clear()


def test_endpoint_next_sentence_returns_null_when_no_material(tmp_db):
    with tmp_db() as db:
        target = _seed_lemma(db, form="λόγος")
        db.commit()
        lemma_id = target.lemma_id

    client = _http_client(tmp_db)
    try:
        resp = client.get("/api/reviews/next-sentence", params={
            "lemma_id": lemma_id,
            "language_code": "el",
        })
        assert resp.status_code == 200
        assert resp.json() is None
    finally:
        app.dependency_overrides.clear()


def test_endpoint_session_returns_bundle(tmp_db):
    with tmp_db() as db:
        a = _seed_lemma(db, form="λόγος")
        _seed_acquiring(db, a.lemma_id, box=1)
        _seed_sentence(db, lemma_surfaces=[(a.lemma_id, "λόγος")])
        db.commit()

    client = _http_client(tmp_db)
    try:
        resp = client.get("/api/reviews/session", params={"language_code": "el", "limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert "sentences" in body and "intro_cards" in body
        assert len(body["sentences"]) == 1
        # Newly-introduced acquiring lemma → emits a "new" intro card
        assert len(body["intro_cards"]) == 1
        card = body["intro_cards"][0]
        assert card["intro_kind"] == "new"
        assert card["lemma_form"] == "λόγος"
    finally:
        app.dependency_overrides.clear()


def test_endpoint_session_unknown_language(tmp_db):
    client = _http_client(tmp_db)
    try:
        resp = client.get("/api/reviews/session", params={"language_code": "zz"})
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()
