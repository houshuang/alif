from app.models import Lemma, Root, UserLemmaKnowledge, PatternInfo
from app.services.fsrs_service import create_new_card


def _seed_root_with_words(db_session, root_letters="ك.ت.ب", meaning="writing"):
    root = Root(root=root_letters, core_meaning_en=meaning)
    db_session.add(root)
    db_session.flush()

    words = []
    for ar, bare, gloss, wazn, wazn_m, pos in [
        ("كِتَاب", "كتاب", "book", "fi'al", "noun of action", "noun"),
        ("كَاتِب", "كاتب", "writer", "fa'il", "doer/agent", "noun"),
        ("مَكْتَبَة", "مكتبة", "library", "maf'ala", "place noun", "noun"),
    ]:
        lemma = Lemma(
            lemma_ar=ar, lemma_ar_bare=bare, gloss_en=gloss,
            root_id=root.root_id, source="test",
            wazn=wazn, wazn_meaning=wazn_m, pos=pos,
        )
        db_session.add(lemma)
        db_session.flush()

        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            fsrs_card_json=create_new_card(),
            source="test", times_seen=5, times_correct=4,
        )
        db_session.add(ulk)
        words.append(lemma)

    db_session.commit()
    return root, words


def test_list_roots(client, db_session):
    root, _ = _seed_root_with_words(db_session)
    resp = client.get("/api/roots")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    item = next(r for r in data if r["root_id"] == root.root_id)
    assert item["root"] == "ك.ت.ب"
    assert item["core_meaning_en"] == "writing"
    assert item["total_words"] == 3
    assert item["known_words"] == 3
    assert item["coverage_pct"] == 100.0
    assert item["has_enrichment"] is False


def test_list_roots_with_enrichment(client, db_session):
    root, _ = _seed_root_with_words(db_session)
    root.enrichment_json = {"etymology_story": "test"}
    db_session.commit()

    resp = client.get("/api/roots")
    data = resp.json()
    item = next(r for r in data if r["root_id"] == root.root_id)
    assert item["has_enrichment"] is True


def test_get_root_detail(client, db_session):
    root, words = _seed_root_with_words(db_session)
    resp = client.get(f"/api/roots/{root.root_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["root"] == "ك.ت.ب"
    assert data["core_meaning_en"] == "writing"
    assert data["enrichment"] is None
    assert data["total_words"] == 3
    assert len(data["patterns"]) >= 1
    all_words = [w for p in data["patterns"] for w in p["words"]]
    assert len(all_words) == 3
    assert all(w["knowledge_state"] == "known" for w in all_words)


def test_get_root_detail_with_enrichment(client, db_session):
    root, _ = _seed_root_with_words(db_session)
    enrichment = {"etymology_story": "The root k-t-b..."}
    root.enrichment_json = enrichment
    db_session.commit()

    resp = client.get(f"/api/roots/{root.root_id}")
    data = resp.json()
    assert data["enrichment"] == enrichment


def test_get_root_not_found(client):
    resp = client.get("/api/roots/99999")
    assert resp.status_code == 404


def test_root_excludes_variants(client, db_session):
    root, words = _seed_root_with_words(db_session)
    # Add a variant (canonical_lemma_id set)
    variant = Lemma(
        lemma_ar="كُتُب", lemma_ar_bare="كتب", gloss_en="books",
        root_id=root.root_id, source="test",
        canonical_lemma_id=words[0].lemma_id,
    )
    db_session.add(variant)
    db_session.commit()

    resp = client.get(f"/api/roots/{root.root_id}")
    data = resp.json()
    assert data["total_words"] == 3  # variant excluded


def test_pattern_list_has_enrichment(client, db_session):
    _seed_root_with_words(db_session)
    resp = client.get("/api/patterns")
    data = resp.json()
    for item in data:
        assert "has_enrichment" in item
        assert item["has_enrichment"] is False


def test_pattern_detail_has_enrichment(client, db_session):
    _seed_root_with_words(db_session)
    # Add pattern enrichment
    pi = PatternInfo(wazn="fa'il", wazn_meaning="doer/agent", enrichment_json={"explanation": "test"})
    db_session.add(pi)
    db_session.commit()

    resp = client.get("/api/patterns/fa'il")
    data = resp.json()
    assert data["enrichment"] == {"explanation": "test"}


def test_word_detail_has_root_id(client, db_session):
    root, words = _seed_root_with_words(db_session)
    resp = client.get(f"/api/words/{words[0].lemma_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["root_id"] == root.root_id
