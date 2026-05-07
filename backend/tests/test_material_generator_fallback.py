import fcntl
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import Lemma, UserLemmaKnowledge
from app.services import material_generator


def test_self_correct_batch_falls_back_to_legacy_generation(db_session):
    """Claude-tool self-correct is CLI-only; quota failures must use API-backed legacy generation."""
    db_session.add_all([
        Lemma(
            lemma_id=1,
            lemma_ar="كِتَابٌ",
            lemma_ar_bare="كتاب",
            gloss_en="book",
            pos="noun",
        ),
        Lemma(
            lemma_id=2,
            lemma_ar="بَيْتٌ",
            lemma_ar_bare="بيت",
            gloss_en="house",
            pos="noun",
        ),
        UserLemmaKnowledge(lemma_id=1, knowledge_state="acquiring"),
        UserLemmaKnowledge(lemma_id=2, knowledge_state="known", fsrs_card_json={"stability": 10}),
    ])
    db_session.commit()

    with (
        patch("app.services.material_generator._generate_via_self_correct") as self_correct,
        patch("app.services.material_generator._generate_via_legacy_batch") as legacy,
    ):
        self_correct.side_effect = RuntimeError(
            "claude exited 1: You're out of extra usage · resets later"
        )
        legacy.return_value = []

        result = material_generator.batch_generate_material([1], count_per_word=1)

    assert result["generated"] == 0
    assert result["words_failed"] == [1]
    self_correct.assert_called_once()
    legacy.assert_called_once()


def test_self_correct_batch_uses_fast_timeout(monkeypatch):
    monkeypatch.delenv("ALIF_SELF_CORRECT_BATCH_TIMEOUT", raising=False)
    called = {}

    def fake_generate(**kwargs):
        called["timeout"] = kwargs["timeout"]
        return SimpleNamespace(sentences_by_target={})

    with (
        patch("app.services.llm.claude_cli_temporarily_disabled", return_value=False),
        patch(
            "app.services.sentence_self_correct.generate_sentences_self_correct_batch",
            side_effect=fake_generate,
        ),
    ):
        material_generator._generate_via_self_correct(
            [{"lemma_id": i} for i in range(10)],
            count_per_word=1,
        )

    assert called["timeout"] == 180


def test_self_correct_batch_honors_cli_cooldown():
    with (
        patch("app.services.llm.claude_cli_temporarily_disabled", return_value=True),
        patch(
            "app.services.sentence_self_correct.generate_sentences_self_correct_batch"
        ) as batch,
        pytest.raises(RuntimeError, match="temporarily disabled"),
    ):
        material_generator._generate_via_self_correct(
            [{"lemma_id": 1}],
            count_per_word=1,
        )

    batch.assert_not_called()


def test_warm_sentence_cache_skips_when_material_lock_busy(tmp_path, monkeypatch):
    lock_path = tmp_path / "alif-update-material.lock"
    monkeypatch.setenv("ALIF_UPDATE_MATERIAL_LOCK", str(lock_path))
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    handle = lock_path.open("w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with patch("app.services.material_generator._warm_sentence_cache_impl") as impl:
            result = material_generator.warm_sentence_cache()
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()

    assert result == {"skipped": True, "reason": "material_update_active"}
    impl.assert_not_called()
