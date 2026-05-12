from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import scripts.update_material as update_material
from app.models import Lemma, UserLemmaKnowledge
from app.services.llm import AllProvidersFailed
from app.services.pipeline_tiers import WordTier


def test_has_diacritics_detects_harakat():
    assert update_material._has_diacritics("كَتَبَ")
    assert not update_material._has_diacritics("كتب")
    assert not update_material._has_diacritics(None)


@patch("app.services.llm.generate_completion")
def test_generate_corpus_enrichment_batch_uses_structured_batch_call(mock_generate):
    mock_generate.return_value = {
        "sentences": [
            {
                "id": 10,
                "diacritized": " كَتَبَ الوَلَدُ ",
                "translation": " The boy wrote. ",
            },
            {"id": 11, "diacritized": "", "translation": "The girl read."},
            {"id": 999, "diacritized": "ignored", "translation": "ignored"},
        ]
    }
    sentences = [
        SimpleNamespace(id=10, arabic_text="كتب الولد"),
        SimpleNamespace(id=11, arabic_text="قَرَأَتِ البنت"),
    ]

    out = update_material._generate_corpus_enrichment_batch(sentences)

    assert out == {
        10: {"diacritized": "كَتَبَ الوَلَدُ", "translation": "The boy wrote."},
        11: {"diacritized": "", "translation": "The girl read."},
    }
    assert mock_generate.call_count == 1
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["json_schema"] == update_material._CORPUS_ENRICH_SCHEMA
    assert kwargs["task_type"] == "corpus_enrichment"
    assert "id=10" in kwargs["prompt"]
    assert "id=11" in kwargs["prompt"]


@patch("app.services.llm.generate_completion")
def test_generate_corpus_enrichment_batch_returns_empty_on_provider_failure(mock_generate):
    mock_generate.side_effect = AllProvidersFailed("no provider")

    out = update_material._generate_corpus_enrichment_batch([
        SimpleNamespace(id=12, arabic_text="ذهب الرجل"),
    ])

    assert out == {}


def _seed_due_lemma(db_session, lemma_id: int) -> None:
    db_session.add(Lemma(
        lemma_id=lemma_id,
        lemma_ar=f"كلمة{lemma_id}",
        lemma_ar_bare=f"كلمة{lemma_id}",
        gloss_en=f"word {lemma_id}",
        pos="noun",
    ))
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="known",
        fsrs_card_json={
            "due": datetime.now(timezone.utc).isoformat(),
            "stability": 1.0,
        },
    ))


def test_step_a_budget_caps_generation_below_pipeline_cap(db_session):
    for lemma_id in range(1, 20):
        _seed_due_lemma(db_session, lemma_id)
    db_session.commit()

    generated = update_material.step_backfill_sentences(
        db_session,
        dry_run=True,
        model="claude_sonnet",
        delay=0.0,
        max_sentences=2000,
        max_step_a_sentences=5,
    )

    assert generated == 5


def test_step_a_batch_misses_do_not_fall_back_to_single_sessions(db_session, capsys):
    _seed_due_lemma(db_session, 1)
    db_session.commit()
    tier_lookup = {
        1: WordTier(
            lemma_id=1,
            due_dt=datetime.now(timezone.utc),
            tier=1,
            backfill_target=3,
            cap_floor=2,
        )
    }

    with (
        patch("app.services.material_generator.batch_generate_material") as batch,
        patch("scripts.update_material.generate_material_for_word") as single,
    ):
        batch.return_value = {"generated": 0, "words_covered": 0, "words_failed": [1]}
        generated = update_material.step_backfill_sentences(
            db_session,
            dry_run=False,
            model="claude_sonnet",
            delay=0.0,
            max_sentences=2000,
            max_step_a_sentences=5,
            tier_lookup=tier_lookup,
        )

    assert generated == 0
    batch.assert_called_once_with([1], model_override="claude_sonnet")
    single.assert_not_called()
    assert "Skipping single-word fallback for 1 batch misses" in capsys.readouterr().out


def test_cron_lemma_enrichment_is_opt_in(monkeypatch):
    monkeypatch.delenv("ALIF_RUN_CRON_LEMMA_ENRICHMENT", raising=False)
    assert update_material._run_lemma_enrichment(False) is False

    monkeypatch.setenv("ALIF_RUN_CRON_LEMMA_ENRICHMENT", "1")
    assert update_material._run_lemma_enrichment(False) is True

    monkeypatch.setenv("ALIF_RUN_CRON_LEMMA_ENRICHMENT", "0")
    assert update_material._run_lemma_enrichment(True) is True
