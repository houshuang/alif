from app.models import Lemma, UserLemmaKnowledge


def _add_lemma(
    db_session,
    arabic: str,
    bare: str,
    transliteration: str | None,
    state: str | None = None,
) -> Lemma:
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=bare,
        gloss_en="immersed",
        pos="adj",
        transliteration_ala_lc=transliteration,
    )
    db_session.add(lemma)
    db_session.flush()
    if state:
        db_session.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=state))
    db_session.commit()
    return lemma


def test_word_lookup_recomputes_transliteration_from_vocalized_lemma(client, db_session):
    lemma = _add_lemma(db_session, "مَغْمُور", "مغمور", "maghmwr")

    resp = client.get(f"/api/review/word-lookup/{lemma.lemma_id}")

    assert resp.status_code == 200
    assert resp.json()["transliteration"] == "maghmūr"


def test_word_lookup_keeps_stored_transliteration_for_unvocalized_lemma(client, db_session):
    lemma = _add_lemma(db_session, "مغمور", "مغمور", "maghmūr")

    resp = client.get(f"/api/review/word-lookup/{lemma.lemma_id}")

    assert resp.status_code == 200
    assert resp.json()["transliteration"] == "maghmūr"


def test_wrap_up_recomputes_transliteration_from_vocalized_lemma(client, db_session):
    lemma = _add_lemma(db_session, "مَغْمُور", "مغمور", "maghmwr", state="acquiring")

    resp = client.post(
        "/api/review/wrap-up",
        json={"seen_lemma_ids": [lemma.lemma_id], "missed_lemma_ids": []},
    )

    assert resp.status_code == 200
    assert resp.json()["cards"][0]["transliteration"] == "maghmūr"
