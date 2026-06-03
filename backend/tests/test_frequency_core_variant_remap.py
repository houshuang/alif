"""Invariant: no active frequency_core_entry may point to a *variant* lemma.
Variant detection links inflected forms (نَزَّلنَا) to a canonical (نزل) after the
entry was mapped; remap_variant_frequency_core_entries heals that — re-pointing to
the canonical, or excluding the entry when it duplicates the canonical's own."""
from app.models import FrequencyCoreEntry, Lemma
from app.services.frequency_core_intake import remap_variant_frequency_core_entries


def _lemma(db, lemma_id, ar, canonical=None):
    db.add(Lemma(lemma_id=lemma_id, lemma_ar=ar, lemma_ar_bare=ar, gloss_en=f"g{lemma_id}",
                 pos="verb", canonical_lemma_id=canonical))
    db.flush()


def _fce(db, rank, lemma_id, display):
    db.add(FrequencyCoreEntry(core_rank=rank, lemma_id=lemma_id, lemma_key=f"lemma:{lemma_id}",
                              display_form=display, score=1.0))
    db.flush()


def _resolves_to_variant(db):
    redirect = {lid: c for lid, c in db.query(Lemma.lemma_id, Lemma.canonical_lemma_id)
                .filter(Lemma.canonical_lemma_id.isnot(None)).all()}
    bad = []
    for e in db.query(FrequencyCoreEntry).filter(FrequencyCoreEntry.excluded_reason.is_(None),
                                                 FrequencyCoreEntry.lemma_id.isnot(None)).all():
        if e.lemma_id in redirect:
            bad.append(e.core_rank)
    return bad


def test_variant_entry_repointed_or_deduped(db_session):
    # Canonical 100 (نزل) has NO entry; variant 101 (نزّلنا) carries the entry -> RE-POINT.
    _lemma(db_session, 100, "نزل")
    _lemma(db_session, 101, "نزّلنا", canonical=100)
    _fce(db_session, 527, 101, "نَزَّلنَا")
    # Canonical 200 (حبّ) HAS its own entry; variant 201 (تحبّ) also has one -> EXCLUDE the variant.
    _lemma(db_session, 200, "حبّ"); _fce(db_session, 50, 200, "حبّ")
    _lemma(db_session, 201, "تحبّ", canonical=200); _fce(db_session, 124, 201, "تُحِبُّ")
    db_session.commit()

    res = remap_variant_frequency_core_entries(db_session)
    assert res["remapped"] == 1 and res["excluded"] == 1

    repointed = db_session.query(FrequencyCoreEntry).filter_by(core_rank=527).first()
    assert repointed.lemma_id == 100 and repointed.display_form == "نزل"

    deduped = db_session.query(FrequencyCoreEntry).filter_by(core_rank=124).first()
    assert deduped.excluded_reason == "duplicate_variant_of_canonical"

    # Invariant holds, and the op is idempotent.
    assert _resolves_to_variant(db_session) == []
    assert remap_variant_frequency_core_entries(db_session) == {"remapped": 0, "excluded": 0}
