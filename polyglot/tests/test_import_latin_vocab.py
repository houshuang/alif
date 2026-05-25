"""Latin vocab importer: DCC frequency ingest/promote, LLPSI assumed-known
marking, idempotency, and cross-language isolation. Uses small fixture files —
no real DCC/LLPSI data or network needed."""
import pytest

from app.models import FrequencyEntry, Lemma, Language, UserLemmaKnowledge
from scripts import import_latin_vocab as imp


def _add_latin(db):
    db.add(Language(code="la", name="Latin", script="latin",
                    direction="ltr", accent_display="macrons_off"))
    db.commit()


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_dcc_ingest_and_promote_idempotent(tmp_db, tmp_path):
    dcc = _write(tmp_path / "dcc_core.tsv",
                 "Headword\tDefinition\tPart of Speech\tRank\n"
                 "sum\tto be\tverb\t1\n"
                 "rex\tking\tnoun\t2\n"
                 "et\tand\tconjunction\t3\n")
    with tmp_db() as db:
        _add_latin(db)
        n = imp.phase_frequency(db, dcc, imp.SOURCE_DCC)
        assert n == 3
        assert db.query(FrequencyEntry).filter(
            FrequencyEntry.language_code == "la",
            FrequencyEntry.source == imp.SOURCE_DCC,
        ).count() == 3

        created = imp.phase_promote(db, imp.SOURCE_DCC)
        assert created == 3
        rex = db.query(Lemma).filter(Lemma.language_code == "la",
                                     Lemma.lemma_bare == "rex").first()
        assert rex is not None
        assert rex.source == "frequency_core"
        assert rex.gloss_en == "king"
        assert rex.frequency_rank == 2
        # "et" is a function word → categorized, still a lemma (mappable)
        et = db.query(Lemma).filter(Lemma.lemma_bare == "et").first()
        assert et.word_category == "function_word"

        # Re-running promote creates nothing new (frequency entries now linked)
        assert imp.phase_promote(db, imp.SOURCE_DCC) == 0
        # Re-ingest replaces, doesn't duplicate
        assert imp.phase_frequency(db, dcc, imp.SOURCE_DCC) == 3
        assert db.query(FrequencyEntry).filter(
            FrequencyEntry.source == imp.SOURCE_DCC).count() == 3


def test_llpsi_marks_assumed_known_no_card(tmp_db, tmp_path):
    llpsi = _write(tmp_path / "llpsi_fr.tsv",
                   "lemma\tgloss\tchapter\n"
                   "puella\tgirl\t1\n"
                   "villa\thouse\t1\n"
                   "cōnsul\tconsul\t2\n"   # macron'd source → stored macron-free
                   "et\tand\t1\n")  # function word: created but not enrolled
    with tmp_db() as db:
        _add_latin(db)
        touched, marked = imp.phase_llpsi(db, llpsi)
        assert touched == 4
        assert marked == 3  # puella, villa, consul — not "et"

        # Latin display policy: display form == normalized key (no macrons, u/i)
        consul = db.query(Lemma).filter(Lemma.lemma_bare == "consul").first()
        assert consul is not None
        assert consul.lemma_form == consul.lemma_bare == "consul"  # macron stripped

        puella = db.query(Lemma).filter(Lemma.lemma_bare == "puella").first()
        assert puella.lemma_form == puella.lemma_bare == "puella"
        assert puella.source == "llpsi"
        assert puella.notes_json == {"llpsi_chapter": "1"}
        ulk = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == puella.lemma_id).first()
        assert ulk.knowledge_state == "known"
        assert ulk.fsrs_card_json is None          # assumed-known scaffold, NO card
        assert ulk.source == "llpsi_known"
        assert ulk.confirmed_at is None            # not yet verified by exposure

        # Function word has no ULK (not a scaffold target)
        et = db.query(Lemma).filter(Lemma.lemma_bare == "et").first()
        assert db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == et.lemma_id).first() is None

        # Idempotent: re-run marks nothing new, no duplicate ULK/Lemma
        touched2, marked2 = imp.phase_llpsi(db, llpsi)
        assert marked2 == 0
        assert db.query(Lemma).filter(Lemma.lemma_bare == "puella").count() == 1
        assert db.query(UserLemmaKnowledge).count() == 3


def test_llpsi_does_not_overwrite_existing_ulk(tmp_db, tmp_path):
    """If the learner already has a real (carded) ULK for a word, the importer
    must not clobber it back to assumed-known."""
    llpsi = _write(tmp_path / "llpsi_fr.tsv", "lemma\tgloss\nmensa\ttable\n")
    with tmp_db() as db:
        _add_latin(db)
        lemma = Lemma(language_code="la", lemma_form="mensa", lemma_bare="mensa",
                      source="reading_intake")
        db.add(lemma)
        db.flush()
        db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="acquiring",
                                  source="reading_intake", fsrs_card_json={"due": "x"}))
        db.commit()

        _, marked = imp.phase_llpsi(db, llpsi)
        assert marked == 0
        ulk = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lemma.lemma_id).one()
        assert ulk.knowledge_state == "acquiring"  # preserved, not reset to known


def test_import_is_language_scoped(tmp_db, tmp_path):
    """A Latin import must never match or touch a Greek lemma, even when the
    normalized bare key genuinely collides. 'vinum' normalizes to bare 'uinum'
    (v→u); we plant an el lemma on that exact bare to force a real collision."""
    llpsi = _write(tmp_path / "llpsi_fr.tsv", "lemma\tgloss\nvinum\twine\n")
    dcc = _write(tmp_path / "dcc.tsv", "lemma\tgloss\trank\nvinum\twine\t1\n")
    with tmp_db() as db:
        _add_latin(db)
        el_lemma = Lemma(language_code="el", lemma_form="x", lemma_bare="uinum",
                         source="test")  # same normalized bare as Latin vinum
        db.add(el_lemma)
        db.commit()
        el_id = el_lemma.lemma_id

        imp.phase_frequency(db, dcc, imp.SOURCE_DCC)
        imp.phase_promote(db, imp.SOURCE_DCC)
        imp.phase_llpsi(db, llpsi)

        # The Greek lemma is untouched; the Latin lemma is a distinct row.
        la_vinum = db.query(Lemma).filter(Lemma.language_code == "la",
                                          Lemma.lemma_bare == "uinum").first()
        assert la_vinum is not None and la_vinum.lemma_id != el_id
        el_after = db.query(Lemma).filter(Lemma.lemma_id == el_id).one()
        assert el_after.language_code == "el"
        # ULK was created only for the Latin lemma (Greek collision untouched)
        ulk = db.query(UserLemmaKnowledge).one()
        assert ulk.lemma_id == la_vinum.lemma_id


@pytest.mark.slow
def test_canonicalizes_verb_infinitives_via_latincy(tmp_db, tmp_path, monkeypatch):
    """With canonicalization on, LLPSI infinitives are stored under the lemma
    LatinCy produces from reading text (facere→facio), so the learner's known
    verbs actually match what they read. Requires the LatinCy model."""
    monkeypatch.setattr(imp, "_USE_LEMMATIZER", True)
    llpsi = _write(tmp_path / "llpsi_fr.tsv",
                   "lemma\tgloss\nfacere\tto make\ncapere\tto take\nposse\tto be able\n")
    with tmp_db() as db:
        _add_latin(db)
        imp.phase_llpsi(db, llpsi)
        bares = {l.lemma_bare for l in db.query(Lemma).filter(Lemma.language_code == "la").all()}
        assert {"facio", "capio", "possum"} <= bares
        assert "facere" not in bares  # infinitive canonicalized away


def test_csv_alternate_headers(tmp_db, tmp_path):
    """Column detection handles a CSV with word/english headers and macron/j-v
    forms (normalized to the u/i lookup key)."""
    csvf = _write(tmp_path / "vocab.csv", "word,english\nIūlius,Julius\nvīta,life\n")
    with tmp_db() as db:
        _add_latin(db)
        rows = imp.parse_vocab_file(csvf)
        bares = {r.lemma_bare for r in rows}
        assert bares == {"iulius", "uita"}  # macrons stripped, j→i, v→u
