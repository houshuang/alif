"""Moving stale stored word links (app/services/lemma_remap.py)."""
from datetime import datetime, timezone

from app.models import (
    Lemma, LemmaRemapVerdict, QuranicVerse, QuranicVerseWord, Sentence, SentenceWord, Story,
    StoryWord,
)
from app.services import lemma_remap


def _lemma(db, ar, bare, gloss, pos, forms=None):
    lem = Lemma(lemma_ar=ar, lemma_ar_bare=bare, gloss_en=gloss, pos=pos, forms_json=forms,
                gates_completed_at=datetime.now(timezone.utc))
    db.add(lem)
    db.flush()
    return lem


def _seed(db):
    day = _lemma(db, "نَهار", "نهار", "day time", "noun")
    river = _lemma(db, "نَهْر", "نهر", "river", "noun", {"plural": "أَنْهَار"})
    s = Sentence(arabic_text="جلسنا عند النهر وقت النهار", english_translation="We sat by the river by day")
    db.add(s)
    db.flush()
    sw_river = SentenceWord(sentence_id=s.id, position=2, surface_form="النهر", lemma_id=day.lemma_id)
    sw_day = SentenceWord(sentence_id=s.id, position=4, surface_form="النهار", lemma_id=day.lemma_id)
    story = Story(title_en="t", body_ar="عبر النَّهْرَ", source="imported")
    db.add_all([sw_river, sw_day, story])
    db.flush()
    stw = StoryWord(story_id=story.id, position=1, surface_form="النَّهْرَ", lemma_id=day.lemma_id,
                    gloss_en="day time")
    verse = QuranicVerse(surah=2, ayah=25, arabic_text="تَجۡرِي مِن تَحۡتِهَا ٱلۡأَنۡهَٰرُ",
                         english_translation="beneath which rivers flow")
    db.add_all([stw, verse])
    db.flush()
    qw = QuranicVerseWord(verse_id=verse.id, position=3, surface_form="ٱلۡأَنۡهَٰرُ", lemma_id=day.lemma_id)
    db.add(qw)
    db.commit()
    return day, river, sw_river, sw_day, stw, qw


def test_spelling_guards():
    assert lemma_remap.fold_quranic("ٱلۡأَنۡهَٰرُ") == "الْأَنْهَارُ"
    # كذلك spells ذلك (proclitic ك), so a link on ذلك never moves to ذُلّ.
    assert lemma_remap.spells_headword("كذلك", "ذَلِكَ")
    assert not lemma_remap.spells_headword("النهر", "نَهار")


def test_finds_moves_in_all_three_tables(db_session):
    day, river, sw_river, sw_day, stw, qw = _seed(db_session)
    moves, _ = lemma_remap.find_moves(db_session)
    got = {(m.table, m.row_id): m.new for m in moves}
    assert got == {("sentence_words", sw_river.id): river.lemma_id,
                   ("story_words", stw.id): river.lemma_id,
                   ("quranic_verse_words", qw.id): river.lemma_id}


def test_judged_moves_apply_once_and_refusals_stay(db_session, monkeypatch):
    day, river, sw_river, sw_day, stw, qw = _seed(db_session)
    calls = []

    def fake(**kw):
        calls.append(kw["prompt"])
        # The Quran plural is refused; the others are the river.
        n = kw["prompt"].count("ITEM ")
        return {"verdicts": [
            {"index": i, "word": "old" if f"ITEM {i}\n  word: ٱلۡأَنۡهَٰرُ" in kw["prompt"] else "new"}
            for i in range(n)]}

    monkeypatch.setattr("app.services.llm.generate_completion", fake)
    stats = lemma_remap.run(apply=True, reverify=False)
    assert stats["moved"] == {"sentence_words": 1, "story_words": 1}
    db_session.expire_all()
    assert db_session.get(SentenceWord, sw_river.id).lemma_id == river.lemma_id
    assert db_session.get(SentenceWord, sw_day.id).lemma_id == day.lemma_id
    st = db_session.get(StoryWord, stw.id)
    assert st.lemma_id == river.lemma_id and st.gloss_en == "river"
    assert db_session.get(QuranicVerseWord, qw.id).lemma_id == day.lemma_id
    assert db_session.query(LemmaRemapVerdict).count() == 3
    assert "We sat by the river" in calls[0]

    # A second run judges nothing new and changes nothing.
    calls.clear()
    again = lemma_remap.run(apply=True, reverify=False)
    assert calls == [] and again["candidates"] == 1 and again["refused_distinct"] == 1


def test_failed_judge_moves_nothing(db_session, monkeypatch):
    day, river, sw_river, *_ = _seed(db_session)

    def boom(**kw):
        raise RuntimeError("cli down")

    monkeypatch.setattr("app.services.llm.generate_completion", boom)
    stats = lemma_remap.run(apply=True, reverify=False)
    assert stats["approved_rows"] == 0 and stats["unjudged_distinct"] == 3
    db_session.expire_all()
    assert db_session.get(SentenceWord, sw_river.id).lemma_id == day.lemma_id
    assert db_session.query(LemmaRemapVerdict).count() == 0
