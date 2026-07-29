"""Safety tests for the exact-subset scaffold importer."""

from datetime import datetime, timezone
import unicodedata

import pytest

from app.models import Lemma, Root
from scripts.import_scaffold_lemmas import (
    ALLOW_HOMOGRAPH,
    SCAFFOLD_ROOTS,
    SCAFFOLD_WORDS,
    import_scaffold_words,
    select_scaffold_words,
)


MOMO_HEADWORDS = ["كُلِّيّ", "إِلٰه", "فَعَلَ"]


def add_momo_roots(db_session) -> dict[str, Root]:
    roots = {
        headword: Root(root=root)
        for headword, root in SCAFFOLD_ROOTS.items()
    }
    db_session.add_all(roots.values())
    db_session.commit()
    return roots


def test_reviewed_momo_inventory_is_registered():
    rows = {
        arabic: (gloss, pos)
        for arabic, gloss, pos in SCAFFOLD_WORDS
    }

    assert rows["كُلِّيّ"] == (
        "total; overall; all-encompassing",
        "adj",
    )
    assert rows["إِلٰه"] == ("god; deity", "noun")
    assert rows["فَعَلَ"] == ("to do", "verb")
    assert set(MOMO_HEADWORDS).issubset(ALLOW_HOMOGRAPH)
    assert SCAFFOLD_ROOTS == {
        "كُلِّيّ": "ك.ل.ل",
        "إِلٰه": "ء.ل.ه",
        "فَعَلَ": "ف.ع.ل",
    }


def test_only_selects_exact_requested_headwords():
    selected = select_scaffold_words(
        ["فَعَلَ", "كُلِّيّ", "فَعَلَ"]
    )

    assert [arabic for arabic, _gloss, _pos in selected] == [
        "كُلِّيّ",
        "فَعَلَ",
    ]


def test_only_accepts_canonically_equivalent_unicode():
    non_nfc = "كُلِّيّ"
    assert non_nfc != "كُلِّيّ"
    assert unicodedata.normalize("NFC", non_nfc) == "كُلِّيّ"

    selected = select_scaffold_words([non_nfc])

    assert selected == [
        ("كُلِّيّ", "total; overall; all-encompassing", "adj")
    ]


def test_only_typo_fails_closed():
    with pytest.raises(ValueError, match="unknown --only"):
        select_scaffold_words(["فعل"])


def test_no_only_preserves_full_importer_behavior():
    assert select_scaffold_words() == SCAFFOLD_WORDS


def test_canonically_equivalent_gated_row_is_idempotent(db_session):
    roots = add_momo_roots(db_session)
    non_nfc = "كُلِّيّ"
    db_session.add(
        Lemma(
            lemma_ar=non_nfc,
            lemma_ar_bare="كلي",
            gloss_en="total; overall; all-encompassing",
            pos="adj",
            source="scaffold",
            root_id=roots["كُلِّيّ"].root_id,
            gates_completed_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    result = import_scaffold_words(
        db_session,
        select_scaffold_words(["كُلِّيّ"]),
        strict_exact=True,
    )

    assert result == {"imported": 0, "resumed": 0, "skipped": 1}
    assert db_session.query(Lemma).count() == 1


def test_interrupted_gate_run_resumes_without_duplicates(db_session):
    roots = add_momo_roots(db_session)
    selected = select_scaffold_words(MOMO_HEADWORDS)

    def interrupt_gates(_db, _lemma_ids, **_kwargs):
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        import_scaffold_words(
            db_session,
            selected,
            strict_exact=True,
            quality_gate_runner=interrupt_gates,
        )

    rows = db_session.query(Lemma).order_by(Lemma.lemma_id).all()
    assert len(rows) == 3
    assert all(row.gates_completed_at is None for row in rows)
    assert {
        row.lemma_ar: row.root.root
        for row in rows
    } == SCAFFOLD_ROOTS

    def stamp_gates(db, lemma_ids, **_kwargs):
        stamped = (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(lemma_ids))
            .update(
                {Lemma.gates_completed_at: datetime.now(timezone.utc)},
                synchronize_session="fetch",
            )
        )
        db.commit()
        return {
            "finalize": {},
            "variants": 0,
            "stamped": stamped,
        }

    result = import_scaffold_words(
        db_session,
        selected,
        strict_exact=True,
        quality_gate_runner=stamp_gates,
    )

    assert result == {"imported": 0, "resumed": 3, "skipped": 0}
    assert db_session.query(Lemma).count() == 3
    assert all(
        row.gates_completed_at is not None
        for row in db_session.query(Lemma).all()
    )


def test_selected_exact_row_with_different_metadata_fails_closed(db_session):
    roots = add_momo_roots(db_session)
    db_session.add(
        Lemma(
            lemma_ar="فَعَلَ",
            lemma_ar_bare="فعل",
            gloss_en="an incompatible sense",
            pos="noun",
            source="other",
            root_id=roots["فَعَلَ"].root_id,
        )
    )
    db_session.commit()

    with pytest.raises(ValueError, match="not the reviewed scaffold entry"):
        import_scaffold_words(
            db_session,
            select_scaffold_words(["فَعَلَ"]),
            strict_exact=True,
        )


def test_selected_import_requires_reviewed_root_inventory(db_session):
    with pytest.raises(ValueError, match="reviewed root ف.ع.ل is missing"):
        import_scaffold_words(
            db_session,
            select_scaffold_words(["فَعَلَ"]),
            strict_exact=True,
        )


def test_selected_exact_row_with_wrong_root_fails_closed(db_session):
    roots = add_momo_roots(db_session)
    wrong_root = Root(root="ف.ع.ي")
    db_session.add(wrong_root)
    db_session.flush()
    db_session.add(
        Lemma(
            lemma_ar="فَعَلَ",
            lemma_ar_bare="فعل",
            gloss_en="to do",
            pos="verb",
            source="scaffold",
            root_id=wrong_root.root_id,
        )
    )
    db_session.commit()

    with pytest.raises(ValueError, match="root_id="):
        import_scaffold_words(
            db_session,
            select_scaffold_words(["فَعَلَ"]),
            strict_exact=True,
        )

    assert roots["فَعَلَ"].root_id != wrong_root.root_id


def test_legacy_unbounded_mode_skips_duplicate_display_identity(db_session):
    for display in ("كُلِّيّ", "كُلِّيّ"):
        db_session.add(
            Lemma(
                lemma_ar=display,
                lemma_ar_bare="كلي",
                gloss_en="legacy row",
                pos="adj",
                source="other",
            )
        )
    db_session.commit()

    result = import_scaffold_words(
        db_session,
        [("كُلِّيّ", "total; overall; all-encompassing", "adj")],
        dry_run=True,
        strict_exact=False,
    )

    assert result == {"imported": 0, "resumed": 0, "skipped": 1}
    assert db_session.query(Lemma).count() == 2
