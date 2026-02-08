from app.models import Root, Lemma, UserLemmaKnowledge, ReviewLog, Sentence, SentenceWord


def test_create_root(db_session):
    root = Root(root="ك.ت.ب", core_meaning_en="writing")
    db_session.add(root)
    db_session.commit()

    fetched = db_session.query(Root).filter_by(root="ك.ت.ب").first()
    assert fetched is not None
    assert fetched.core_meaning_en == "writing"


def test_create_lemma_with_root(db_session):
    root = Root(root="ك.ل.ب", core_meaning_en="dog")
    db_session.add(root)
    db_session.flush()

    lemma = Lemma(
        lemma_ar="كَلْب",
        lemma_ar_bare="كلب",
        root_id=root.root_id,
        pos="noun",
        gloss_en="dog",
        source="duolingo",
    )
    db_session.add(lemma)
    db_session.commit()

    fetched = db_session.query(Lemma).filter_by(lemma_ar_bare="كلب").first()
    assert fetched is not None
    assert fetched.root.root == "ك.ل.ب"
    assert fetched.gloss_en == "dog"


def test_create_knowledge(db_session):
    lemma = Lemma(lemma_ar="بَيْت", lemma_ar_bare="بيت", gloss_en="house")
    db_session.add(lemma)
    db_session.flush()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        source="duolingo",
    )
    db_session.add(knowledge)
    db_session.commit()

    fetched = db_session.query(UserLemmaKnowledge).first()
    assert fetched.knowledge_state == "learning"
    assert fetched.lemma.lemma_ar == "بَيْت"


def test_create_review_log(db_session):
    lemma = Lemma(lemma_ar="كَبير", lemma_ar_bare="كبير", gloss_en="big")
    db_session.add(lemma)
    db_session.flush()

    log = ReviewLog(
        lemma_id=lemma.lemma_id,
        rating=3,
        response_ms=1500,
        session_id="test-session",
    )
    db_session.add(log)
    db_session.commit()

    fetched = db_session.query(ReviewLog).first()
    assert fetched.rating == 3
    assert fetched.response_ms == 1500


def test_sentence_with_words(db_session):
    lemma = Lemma(lemma_ar="كَلْب", lemma_ar_bare="كلب", gloss_en="dog")
    db_session.add(lemma)
    db_session.flush()

    sentence = Sentence(
        arabic_text="هذا كلب",
        english_translation="This is a dog",
        source="manual",
        target_lemma_id=lemma.lemma_id,
    )
    db_session.add(sentence)
    db_session.flush()

    word = SentenceWord(
        sentence_id=sentence.id,
        position=1,
        surface_form="كلب",
        lemma_id=lemma.lemma_id,
        is_target_word=1,
    )
    db_session.add(word)
    db_session.commit()

    fetched = db_session.query(Sentence).first()
    assert len(fetched.words) == 1
    assert fetched.words[0].is_target_word == 1
