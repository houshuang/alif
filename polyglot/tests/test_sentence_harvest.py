"""Sentence-harvest tests.

The harvest service is pure DB compute — no LLM, no network — so these can run
in the fast test tier. We construct Page + PageWord rows directly to avoid
depending on the NLP provider stack.
"""
from datetime import datetime, timezone

from app.models import (
    Lemma, Story, Page, PageWord, Sentence, SentenceWord, UserLemmaKnowledge,
)
from app.services.sentence_harvest import (
    harvest_page_sentences,
    harvest_story_sentences,
    _reconstruct_sentence_text,
)


def _seed_story(db, body="Είμαι εδώ. Φιλοσοφία είναι ωραία."):
    story = Story(language_code="el", title="t", source="paste", body_src=body, page_count=1)
    db.add(story)
    db.flush()
    page = Page(
        story_id=story.id, page_number=1, body_src=body,
        processed_at=datetime.now(timezone.utc),
        mappings_verified_at=datetime.now(timezone.utc),
    )
    db.add(page)
    db.flush()
    return story, page


def _add_lemma(db, form, bare, **kw):
    l = Lemma(language_code="el", lemma_form=form, lemma_bare=bare, source="test", **kw)
    db.add(l)
    db.flush()
    return l


def _add_word(db, page, position, surface, sentence_index, lemma=None):
    pw = PageWord(
        page_id=page.id,
        position=position,
        surface_form=surface,
        sentence_index=sentence_index,
        lemma_id=lemma.lemma_id if lemma else None,
    )
    db.add(pw)
    return pw


def test_reconstruct_text_attaches_punctuation_to_preceding_word(tmp_db):
    with tmp_db() as db:
        # Build PageWord rows without committing — pure list ordering test
        story, page = _seed_story(db)
        words = [
            _add_word(db, page, 0, "Καλημέρα", 0),
            _add_word(db, page, 1, ",", 0),
            _add_word(db, page, 2, "κόσμε", 0),
            _add_word(db, page, 3, "!", 0),
        ]
        text = _reconstruct_sentence_text(words)
        assert text == "Καλημέρα, κόσμε!"


def test_harvest_skips_page_without_verified_mappings(tmp_db):
    with tmp_db() as db:
        story, page = _seed_story(db)
        page.mappings_verified_at = None
        db.commit()
        lemma = _add_lemma(db, "βιβλίο", "βιβλιο")
        _add_word(db, page, 0, "Βιβλίο", 0, lemma)
        db.commit()

        created = harvest_page_sentences(db, page)

        assert created == 0
        assert db.query(Sentence).count() == 0


def test_harvest_creates_one_sentence_per_sentence_index(tmp_db):
    with tmp_db() as db:
        story, page = _seed_story(db)
        l_be = _add_lemma(db, "είμαι", "ειμαι")
        l_here = _add_lemma(db, "εδώ", "εδω")
        l_phil = _add_lemma(db, "φιλοσοφία", "φιλοσοφια")
        l_is = _add_lemma(db, "είναι", "ειναι")
        l_nice = _add_lemma(db, "ωραίος", "ωραιος")

        _add_word(db, page, 0, "Είμαι", 0, l_be)
        _add_word(db, page, 1, "εδώ", 0, l_here)
        _add_word(db, page, 2, ".", 0)
        _add_word(db, page, 3, "Φιλοσοφία", 1, l_phil)
        _add_word(db, page, 4, "είναι", 1, l_is)
        _add_word(db, page, 5, "ωραία", 1, l_nice)
        _add_word(db, page, 6, ".", 1)
        db.commit()

        created = harvest_page_sentences(db, page)

        assert created == 2
        sentences = db.query(Sentence).filter(Sentence.page_id == page.id).order_by(
            Sentence.sentence_index_in_page
        ).all()
        assert [s.sentence_index_in_page for s in sentences] == [0, 1]
        assert sentences[0].text == "Είμαι εδώ."
        assert sentences[1].text == "Φιλοσοφία είναι ωραία."
        assert sentences[0].source == "textbook"
        assert sentences[0].mappings_verified_at == page.mappings_verified_at

        # SentenceWord rows mirror the PageWord rows
        sw0 = db.query(SentenceWord).filter(SentenceWord.sentence_id == sentences[0].id).all()
        assert len(sw0) == 3  # Είμαι, εδώ, .


def test_harvest_is_idempotent(tmp_db):
    with tmp_db() as db:
        story, page = _seed_story(db)
        lemma = _add_lemma(db, "βιβλίο", "βιβλιο")
        _add_word(db, page, 0, "Βιβλίο", 0, lemma)
        _add_word(db, page, 1, ".", 0)
        db.commit()

        first = harvest_page_sentences(db, page)
        second = harvest_page_sentences(db, page)

        assert first == 1
        assert second == 0
        assert db.query(Sentence).filter(Sentence.page_id == page.id).count() == 1


def test_harvest_force_replays(tmp_db):
    with tmp_db() as db:
        story, page = _seed_story(db)
        lemma = _add_lemma(db, "βιβλίο", "βιβλιο")
        _add_word(db, page, 0, "Βιβλίο", 0, lemma)
        _add_word(db, page, 1, ".", 0)
        db.commit()

        # First harvest creates 1 sentence; second (no force) is a no-op.
        assert harvest_page_sentences(db, page) == 1
        assert harvest_page_sentences(db, page) == 0
        first_sentence_id = (
            db.query(Sentence).filter(Sentence.page_id == page.id).one().id
        )
        # Force replays in place — same end state and same sentence id.
        assert harvest_page_sentences(db, page, force=True) == 1
        assert db.query(Sentence).filter(Sentence.page_id == page.id).count() == 1
        assert (
            db.query(Sentence).filter(Sentence.page_id == page.id).one().id
            == first_sentence_id
        )


def test_harvest_skips_caps_headings(tmp_db):
    """A sentence with ≥80% all-caps tokens and ≤10 words is a heading and
    must not become reviewable material — Greek PDFs typeset chapter titles
    in caps without accents, which is meta-text, not vocabulary."""
    with tmp_db() as db:
        story, page = _seed_story(db)
        l_heading = _add_lemma(db, "ΠΟΛΙΤΙΣΜΟΣ", "πολιτισμοσ")
        l_body = _add_lemma(db, "γράφω", "γραφω")

        # Sentence 0: heading — three all-caps tokens
        _add_word(db, page, 0, "ΑΡΧΑΙΟΙ", 0, l_heading)
        _add_word(db, page, 1, "ΕΛΛΗΝΙΚΟΙ", 0, l_heading)
        _add_word(db, page, 2, "ΠΟΛΙΤΙΣΜΟΙ", 0, l_heading)

        # Sentence 1: body — content sentence
        _add_word(db, page, 3, "Γράφω", 1, l_body)
        _add_word(db, page, 4, ".", 1)
        db.commit()

        created = harvest_page_sentences(db, page)
        assert created == 1
        sids = [s.sentence_index_in_page for s in
                db.query(Sentence).filter(Sentence.page_id == page.id).all()]
        assert sids == [1]


def test_harvest_skips_page_boundary_fragments(tmp_db):
    """Page breaks can cut through a sentence or word; those fragments should
    stay in PageWord for the reader but not become reusable review sentences."""
    with tmp_db() as db:
        story, p1 = _seed_story(db, body="Η οργάνωση της παρα")
        story.page_count = 2
        p1.body_clean = "Η οργάνωση της παρα"
        p2 = Page(
            story_id=story.id,
            page_number=2,
            body_src="γωγής ολοκληρώθηκε. Νέα πρόταση.",
            body_clean="γωγής ολοκληρώθηκε. Νέα πρόταση.",
            processed_at=datetime.now(timezone.utc),
            mappings_verified_at=datetime.now(timezone.utc),
        )
        db.add(p2)
        db.flush()

        l_org = _add_lemma(db, "οργάνωση", "οργανωση")
        l_prod = _add_lemma(db, "παραγωγή", "παραγωγη")
        l_new = _add_lemma(db, "νέος", "νεος")

        _add_word(db, p1, 0, "Η", 0)
        _add_word(db, p1, 1, "οργάνωση", 0, l_org)
        _add_word(db, p1, 2, "της", 0)
        _add_word(db, p1, 3, "παρα", 0, l_prod)

        _add_word(db, p2, 0, "γωγής", 0, l_prod)
        _add_word(db, p2, 1, "ολοκληρώθηκε", 0, l_prod)
        _add_word(db, p2, 2, ".", 0)
        _add_word(db, p2, 3, "Νέα", 1, l_new)
        _add_word(db, p2, 4, "πρόταση", 1, l_new)
        _add_word(db, p2, 5, ".", 1)
        db.commit()

        assert harvest_page_sentences(db, p1) == 0
        assert harvest_page_sentences(db, p2) == 1

        sentences = (
            db.query(Sentence)
            .filter(Sentence.page_id.in_([p1.id, p2.id]))
            .order_by(Sentence.page_id, Sentence.sentence_index_in_page)
            .all()
        )
        assert [(s.page_id, s.sentence_index_in_page, s.text) for s in sentences] == [
            (p2.id, 1, "Νέα πρόταση."),
        ]


def test_harvest_resolves_variant_lemmas_to_canonical(tmp_db):
    """SentenceWord rows must carry the canonical lemma_id — never the variant.
    Defense-in-depth for Hard Invariant #9 applied at storage time."""
    with tmp_db() as db:
        story, page = _seed_story(db)
        canonical = _add_lemma(db, "βιβλίο", "βιβλιο")
        variant = _add_lemma(
            db, "βιβλίον", "βιβλιον",
            canonical_lemma_id=canonical.lemma_id,
        )
        _add_word(db, page, 0, "Βιβλίον", 0, variant)
        _add_word(db, page, 1, ".", 0)
        db.commit()

        harvest_page_sentences(db, page)

        sw = db.query(SentenceWord).filter(SentenceWord.lemma_id.isnot(None)).first()
        assert sw.lemma_id == canonical.lemma_id


def test_harvest_skips_punctuation_only_sentences(tmp_db):
    """Tokenizer artefacts (e.g. a stray line break giving a sentence with
    only `\\n` or `--`) carry no content and must not produce Sentence rows."""
    with tmp_db() as db:
        story, page = _seed_story(db)
        lemma = _add_lemma(db, "λέξη", "λεξη")

        # Sentence 0: punctuation only
        _add_word(db, page, 0, "—", 0)
        _add_word(db, page, 1, "—", 0)

        # Sentence 1: real content
        _add_word(db, page, 2, "Λέξη", 1, lemma)
        _add_word(db, page, 3, ".", 1)
        db.commit()

        created = harvest_page_sentences(db, page)
        assert created == 1


def test_harvest_story_walks_verified_pages(tmp_db):
    with tmp_db() as db:
        story, p1 = _seed_story(db)
        # Add second page, also verified
        p2 = Page(
            story_id=story.id, page_number=2, body_src="x",
            processed_at=datetime.now(timezone.utc),
            mappings_verified_at=datetime.now(timezone.utc),
        )
        db.add(p2)
        db.flush()
        # Add a third page that's NOT verified — must be skipped
        p3 = Page(
            story_id=story.id, page_number=3, body_src="y",
            processed_at=datetime.now(timezone.utc),
            mappings_verified_at=None,
        )
        db.add(p3)
        db.flush()

        l1 = _add_lemma(db, "α", "α")
        l2 = _add_lemma(db, "β", "β")
        _add_word(db, p1, 0, "α", 0, l1)
        _add_word(db, p1, 1, ".", 0)
        _add_word(db, p2, 0, "β", 0, l2)
        _add_word(db, p2, 1, ".", 0)
        # p3 has a word but won't be harvested
        _add_word(db, p3, 0, "γ", 0, l1)
        db.commit()

        total = harvest_story_sentences(db, story.id)
        assert total == 2
        assert db.query(Sentence).filter(Sentence.page_id == p3.id).count() == 0
