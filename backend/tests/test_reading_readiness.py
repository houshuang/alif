from datetime import datetime, timezone

from app.models import Lemma
from scripts import reading_readiness


def test_reading_readiness_resolves_exact_function_alias(db_session):
    now = datetime.now(timezone.utc)
    qad = Lemma(
        lemma_ar="قَدْ",
        lemma_ar_bare="قد",
        gloss_en="already",
        pos="particle",
        gates_completed_at=now,
    )
    loss = Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=now,
    )
    db_session.add_all([qad, loss])
    db_session.commit()

    result = reading_readiness.analyze(db_session, "فَقَدْ", top=10)

    assert result["counts"]["total"] == 1
    assert result["counts"]["function"] == 1
    assert result["counts"]["new_in_vocab"] == 0
    assert result["counts"]["unmapped"] == 0
    assert result["top_unlocks"] == []


def test_reading_readiness_unresolved_alias_is_a_gap_not_free_function(
    db_session,
    monkeypatch,
):
    db_session.add(Lemma(
        lemma_ar="فَقْد",
        lemma_ar_bare="فقد",
        gloss_en="loss",
        pos="noun",
        gates_completed_at=datetime.now(timezone.utc),
    ))
    db_session.commit()
    monkeypatch.setattr(
        reading_readiness,
        "_camel_content_lemma",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unresolved exact alias must not reach CAMeL")
        ),
    )

    result = reading_readiness.analyze(db_session, "فَقَدْ", top=10)

    assert result["counts"]["total"] == 1
    assert result["counts"]["function"] == 0
    assert result["counts"]["unmapped"] == 1
    assert result["top_unlocks"] == [{
        "kind": "unmapped",
        "lemma_id": None,
        "display": "فقد",
        "gloss": None,
        "count": 1,
    }]
