"""Lemma-repair primitives. These mutate the user's own study history (ULK,
review_log) so the FK migration and ULK consolidation must be exact."""
from datetime import datetime, timezone

from app.models import (
    Lemma, UserLemmaKnowledge, ReviewLog, Sentence, SentenceWord, Page,
    PageWord, Story, FrequencyEntry, ContentFlag,
)
from app.services.lemma_integrity import apply_citation_fix, merge_lemma_into


def _lemma(db, form, bare, **kw):
    l = Lemma(language_code="el", source="test", lemma_form=form, lemma_bare=bare, **kw)
    db.add(l); db.flush()
    return l


def _ulk(db, lemma_id, state="acquiring", **kw):
    u = UserLemmaKnowledge(lemma_id=lemma_id, knowledge_state=state, **kw)
    db.add(u); db.flush()
    return u


def test_rename_in_place_when_citation_absent(tmp_db):
    with tmp_db() as db:
        bad = _lemma(db, "εξελίχθηκαν", "εξελιχθηκαν", gloss_en="to develop")
        db.commit()
        res = apply_citation_fix(db, bad.lemma_id, "εξελίσσομαι", pos="verb",
                                 gloss="to evolve, develop")
        db.commit()
        assert res.action == "rename"
        row = db.get(Lemma, bad.lemma_id)
        assert row.lemma_form == "εξελίσσομαι"
        assert row.lemma_bare == "εξελισσομαι"
        assert row.pos == "verb"
        assert row.gloss_en == "to evolve, develop"


def test_noop_when_already_correct(tmp_db):
    with tmp_db() as db:
        good = _lemma(db, "βιβλίο", "βιβλιο", gloss_en="book")
        db.commit()
        res = apply_citation_fix(db, good.lemma_id, "βιβλίο", pos="noun")
        db.commit()
        assert res.action == "noop"
        assert db.get(Lemma, good.lemma_id).pos == "noun"


def test_merge_repoints_all_fks_and_deletes_duplicate(tmp_db):
    with tmp_db() as db:
        canonical = _lemma(db, "κάθισμα", "καθισμα", gloss_en="seat")
        dup = _lemma(db, "καθίσματα", "καθισματα", gloss_en="seats")
        story = Story(language_code="el", title="t", source="paste"); db.add(story); db.flush()
        page = Page(story_id=story.id, page_number=1, body_src="x"); db.add(page); db.flush()
        sent = Sentence(language_code="el", text="...", source="llm",
                        target_lemma_id=dup.lemma_id)
        db.add(sent); db.flush()
        db.add(SentenceWord(sentence_id=sent.id, position=0, surface_form="καθίσματα",
                            lemma_id=dup.lemma_id))
        db.add(PageWord(page_id=page.id, position=0, surface_form="καθίσματα",
                        lemma_id=dup.lemma_id))
        db.add(ReviewLog(lemma_id=dup.lemma_id, rating=3,
                         reviewed_at=datetime.now(timezone.utc)))
        db.add(FrequencyEntry(language_code="el", source="subtlex_gr", rank=500,
                              lemma_key="καθισματα", display_form="καθίσματα",
                              lemma_id=dup.lemma_id))
        db.add(ContentFlag(content_type="lemma", lemma_id=dup.lemma_id))
        db.commit()

        counts = merge_lemma_into(db, dup.lemma_id, canonical.lemma_id)
        db.commit()

        assert db.get(Lemma, dup.lemma_id) is None
        assert db.query(Sentence).filter_by(id=sent.id).first().target_lemma_id == canonical.lemma_id
        assert db.query(SentenceWord).first().lemma_id == canonical.lemma_id
        assert db.query(PageWord).first().lemma_id == canonical.lemma_id
        assert db.query(ReviewLog).first().lemma_id == canonical.lemma_id
        assert db.query(FrequencyEntry).first().lemma_id == canonical.lemma_id
        assert db.query(ContentFlag).first().lemma_id == canonical.lemma_id
        # surface form text is preserved even though lemma_id moved
        assert db.query(SentenceWord).first().surface_form == "καθίσματα"


def test_merge_consolidates_ulk_keeping_more_advanced_state(tmp_db):
    with tmp_db() as db:
        canonical = _lemma(db, "κάθισμα", "καθισμα")
        dup = _lemma(db, "καθίσματα", "καθισματα")
        _ulk(db, canonical.lemma_id, state="acquiring", times_seen=2, times_correct=1,
             acquisition_box=1)
        _ulk(db, dup.lemma_id, state="known", times_seen=5, times_correct=4,
             acquisition_box=3)
        db.commit()

        merge_lemma_into(db, dup.lemma_id, canonical.lemma_id)
        db.commit()

        ulks = db.query(UserLemmaKnowledge).all()
        assert len(ulks) == 1
        kept = ulks[0]
        assert kept.lemma_id == canonical.lemma_id
        assert kept.knowledge_state == "known"     # more advanced wins
        assert kept.acquisition_box == 3
        assert kept.times_seen == 7                 # summed
        assert kept.times_correct == 5


def test_merge_repoints_ulk_when_target_has_none(tmp_db):
    with tmp_db() as db:
        canonical = _lemma(db, "κάθισμα", "καθισμα")
        dup = _lemma(db, "καθίσματα", "καθισματα")
        _ulk(db, dup.lemma_id, state="learning", times_seen=3)
        db.commit()

        merge_lemma_into(db, dup.lemma_id, canonical.lemma_id)
        db.commit()

        ulks = db.query(UserLemmaKnowledge).all()
        assert len(ulks) == 1
        assert ulks[0].lemma_id == canonical.lemma_id
        assert ulks[0].knowledge_state == "learning"


def test_apply_fix_merges_when_citation_already_exists(tmp_db):
    with tmp_db() as db:
        canonical = _lemma(db, "κάθισμα", "καθισμα", gloss_en="seat")
        dup = _lemma(db, "καθίσματα", "καθισματα", gloss_en="seats")
        _ulk(db, dup.lemma_id, state="acquiring", times_seen=1)
        db.commit()
        res = apply_citation_fix(db, dup.lemma_id, "κάθισμα", pos="noun")
        db.commit()
        assert res.action == "merge"
        assert res.target_id == canonical.lemma_id
        assert db.get(Lemma, dup.lemma_id) is None
        assert db.query(UserLemmaKnowledge).first().lemma_id == canonical.lemma_id


def test_self_ref_pointers_move_on_merge(tmp_db):
    with tmp_db() as db:
        canonical = _lemma(db, "κάθισμα", "καθισμα")
        dup = _lemma(db, "καθίσματα", "καθισματα")
        # another lemma names `dup` as its canonical/cognate
        other = _lemma(db, "καθισματάκι", "καθισματακι", canonical_lemma_id=dup.lemma_id)
        cog = _lemma(db, "κάθισμαAG", "καθισμααg", cognate_lemma_id=dup.lemma_id)
        db.commit()
        merge_lemma_into(db, dup.lemma_id, canonical.lemma_id)
        db.commit()
        assert db.get(Lemma, other.lemma_id).canonical_lemma_id == canonical.lemma_id
        assert db.get(Lemma, cog.lemma_id).cognate_lemma_id == canonical.lemma_id
