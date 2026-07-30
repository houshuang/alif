import argparse
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import scripts.update_material as update_material
from app.models import Lemma, UserLemmaKnowledge
from app.services.llm import AllProvidersFailed
from app.services.pipeline_tiers import WordTier


def test_has_diacritics_detects_harakat():
    assert update_material._has_diacritics("كَتَبَ")
    assert not update_material._has_diacritics("كَتب")
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
    assert kwargs["model_override"] == "claude_haiku"
    assert "id=10" in kwargs["prompt"]
    assert "id=11" in kwargs["prompt"]
    assert "exact non-diacritic content tokens" in kwargs["prompt"]
    assert '["كتب", "الولد"]' in kwargs["prompt"]


@patch("app.services.llm.generate_completion")
def test_generate_corpus_enrichment_batch_honors_controlled_provider(
    mock_generate,
    monkeypatch,
):
    mock_generate.return_value = {"sentences": []}
    monkeypatch.setenv("ALIF_CORPUS_ENRICH_PROVIDER", "anthropic")

    update_material._generate_corpus_enrichment_batch(
        [SimpleNamespace(id=10, arabic_text="كتب")]
    )

    assert mock_generate.call_args.kwargs["model_override"] == "anthropic"


def test_generate_corpus_enrichment_batch_rejects_unknown_provider(
    monkeypatch,
):
    monkeypatch.setenv("ALIF_CORPUS_ENRICH_PROVIDER", "surprise")

    with pytest.raises(ValueError, match="ALIF_CORPUS_ENRICH_PROVIDER"):
        update_material._generate_corpus_enrichment_batch(
            [SimpleNamespace(id=10, arabic_text="كتب")]
        )


@patch("app.services.llm.generate_completion")
def test_generate_corpus_enrichment_batch_returns_empty_on_provider_failure(mock_generate):
    mock_generate.side_effect = AllProvidersFailed("no provider")

    out = update_material._generate_corpus_enrichment_batch([
        SimpleNamespace(id=12, arabic_text="ذهب الرجل"),
    ])

    assert out == {}


def test_corpus_rejected_summary_includes_mapping_blockers():
    result = SimpleNamespace(
        mapping_blocked_ids=[10, 11],
        quality_rejected_ids=[13],
        target_rejected_ids=[10, 14],
    )

    assert update_material._corpus_rejected_count(result) == 4
    assert update_material._corpus_rejected_count(None) == 0


def test_broad_corpus_dry_run_excludes_unrecoverable_claim_sentinels(
    db_session,
):
    args = SimpleNamespace(
        corpus_kind="momo_book",
        corpus_sentence_id=None,
        corpus_limit=1,
        corpus_activate_limit=0,
        corpus_active_ceiling=1950,
        corpus_retry_blocked=False,
        dry_run=True,
    )
    empty_plan = SimpleNamespace(detail=lambda: {})

    with (
        patch(
            "scripts.update_material.plan_corpus_enrichment_report",
            return_value=empty_plan,
        ) as enrichment_plan,
        patch(
            "scripts.update_material.plan_corpus_activation",
            return_value=empty_plan,
        ) as activation_plan,
    ):
        result = update_material._run_scoped_corpus_step(db_session, args)

    assert result is None
    assert (
        enrichment_plan.call_args.kwargs["include_legacy_claims"] is False
    )
    activation_plan.assert_not_called()


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


def test_cron_corpus_enrichment_is_opt_in(monkeypatch):
    monkeypatch.delenv("ALIF_RUN_CRON_CORPUS_ENRICHMENT", raising=False)
    assert update_material._run_corpus_enrichment(False) is False

    monkeypatch.setenv("ALIF_RUN_CRON_CORPUS_ENRICHMENT", "1")
    assert update_material._run_corpus_enrichment(False) is True

    monkeypatch.setenv("ALIF_RUN_CRON_CORPUS_ENRICHMENT", "0")
    assert update_material._run_corpus_enrichment(True) is True


def test_corpus_cli_validation_requires_exact_scope():
    parser = argparse.ArgumentParser(add_help=False)
    args = SimpleNamespace(
        corpus_kind=None,
        corpus_sentence_id=None,
        corpus_limit=20,
        corpus_activate_limit=0,
        corpus_active_ceiling=1950,
    )

    with pytest.raises(SystemExit):
        update_material._validate_corpus_cli_args(
            parser,
            args,
            corpus_requested=True,
        )


def test_corpus_cli_validation_normalizes_intersected_scope():
    parser = argparse.ArgumentParser(add_help=False)
    args = SimpleNamespace(
        corpus_kind="  momo_book ",
        corpus_sentence_id=[12, 10, 12],
        corpus_limit=0,
        corpus_activate_limit=5,
        corpus_active_ceiling=1950,
    )

    update_material._validate_corpus_cli_args(
        parser,
        args,
        corpus_requested=True,
    )

    assert args.corpus_kind == "momo_book"
    assert args.corpus_sentence_id == [10, 12]


def test_corpus_cli_validation_rejects_combined_phases():
    parser = argparse.ArgumentParser(add_help=False)
    args = SimpleNamespace(
        corpus_kind="momo_book",
        corpus_sentence_id=None,
        corpus_limit=20,
        corpus_activate_limit=5,
        corpus_active_ceiling=1950,
    )

    with pytest.raises(SystemExit):
        update_material._validate_corpus_cli_args(
            parser,
            args,
            corpus_requested=True,
        )


def test_corpus_cli_blocked_retry_requires_exact_ids_and_preparation():
    parser = argparse.ArgumentParser(add_help=False)
    args = SimpleNamespace(
        corpus_kind="momo_book",
        corpus_sentence_id=None,
        corpus_limit=1,
        corpus_activate_limit=0,
        corpus_active_ceiling=1950,
        corpus_retry_blocked=True,
    )
    with pytest.raises(SystemExit):
        update_material._validate_corpus_cli_args(
            parser,
            args,
            corpus_requested=True,
        )

    args.corpus_sentence_id = [12]
    args.corpus_limit = 0
    with pytest.raises(SystemExit):
        update_material._validate_corpus_cli_args(
            parser,
            args,
            corpus_requested=True,
        )


def test_corpus_cli_blocked_retry_accepts_exact_preparation_only():
    parser = argparse.ArgumentParser(add_help=False)
    args = SimpleNamespace(
        corpus_kind="momo_book",
        corpus_sentence_id=[12, 12],
        corpus_limit=1,
        corpus_activate_limit=0,
        corpus_active_ceiling=1950,
        corpus_retry_blocked=True,
    )

    update_material._validate_corpus_cli_args(
        parser,
        args,
        corpus_requested=True,
    )

    assert args.corpus_sentence_id == [12]
    assert update_material._corpus_run_kwargs(args)["retry_blocked"] is True


def test_cron_pregeneration_is_opt_in(monkeypatch):
    monkeypatch.delenv("ALIF_RUN_CRON_PREGENERATION", raising=False)
    assert update_material._run_pregeneration(False) is False

    monkeypatch.setenv("ALIF_RUN_CRON_PREGENERATION", "1")
    assert update_material._run_pregeneration(False) is True

    monkeypatch.setenv("ALIF_RUN_CRON_PREGENERATION", "0")
    assert update_material._run_pregeneration(True) is True
