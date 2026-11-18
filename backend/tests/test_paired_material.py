"""Pre-read batches get sentences that practise two batch words together."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import Lemma, UserLemmaKnowledge
from app.services import material_generator as mg


def _lemma(db, i, state="acquiring", category=None):
    lemma = Lemma(lemma_ar=f"كلمة{i}", lemma_ar_bare=f"كلمة{i}", gloss_en=f"w{i}", pos="noun",
                  word_category=category, gates_completed_at=datetime.now(timezone.utc))
    db.add(lemma)
    db.flush()
    db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=state, source="preread"))
    return lemma.lemma_id


def test_pairs_only_acquiring_non_name_words(db_session, monkeypatch):
    words = [_lemma(db_session, i) for i in range(4)]
    name = _lemma(db_session, 10, category="practiced_name")
    known = _lemma(db_session, 11, state="known")
    db_session.commit()

    groups, written = [], []

    def generate(target_words, **kw):
        ids = [w["lemma_id"] for w in target_words]
        groups.append(ids)
        return [SimpleNamespace(target_lemma_ids=ids)]

    monkeypatch.setattr("app.services.sentence_generator.generate_validated_sentences_multi_target", generate)
    monkeypatch.setattr(mg, "validate_multi_target_sentences_batch",
                        lambda db, candidates, lookup: [(res, []) for res, _bares in candidates])
    monkeypatch.setattr(mg, "write_multi_target_sentence", lambda db, res, mappings: written.append(res))

    stats = mg.generate_paired_material(words + [name, known])

    assert sorted(i for g in groups for i in g) == sorted(words)
    assert all(len(g) == 2 for g in groups)
    assert stats == {"pairs": 2, "generated": 2, "words_covered": 4}
    assert len(written) == 2


def test_pair_cap_and_too_few_words(db_session, monkeypatch):
    words = [_lemma(db_session, i) for i in range(6)]
    db_session.commit()
    monkeypatch.setattr("app.services.sentence_generator.generate_validated_sentences_multi_target",
                        lambda target_words, **kw: [])
    assert mg.generate_paired_material(words, max_pairs=1)["pairs"] == 1
    assert mg.generate_paired_material(words[:1]) == {"pairs": 0, "generated": 0, "words_covered": 0}
