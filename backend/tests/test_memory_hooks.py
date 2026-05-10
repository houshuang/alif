from app.services.memory_hooks import hook_quality_reason, prepare_hooks_for_storage


def _hooks(candidate_scores, best_index=0):
    return {
        "candidates": [
            {
                "keyword": f"kw{i}",
                "mnemonic": f"mnemonic {i}",
                "sound_match": sound,
                "interaction": interaction,
                "extraction": extraction,
            }
            for i, (sound, interaction, extraction) in enumerate(candidate_scores)
        ],
        "best_index": best_index,
        "mnemonic": "you see a strong mnemonic",
        "cognates": [],
        "collocations": [{"ar": "مِثَالٌ", "en": "example"}],
        "usage_context": "Used in normal contexts.",
        "fun_fact": None,
    }


def test_prepare_hooks_accepts_strong_candidate_and_strips_scores():
    hooks = _hooks([(5, 5, 4)])

    storage, reason = prepare_hooks_for_storage(hooks)

    assert reason == "strong_candidate"
    assert storage is not None
    assert "candidates" not in storage
    assert "best_index" not in storage
    assert storage["mnemonic"] == "mnemonic 0"


def test_prepare_hooks_stores_winning_candidate_mnemonic():
    hooks = _hooks([(5, 5, 5), (4, 4, 4)], best_index=1)
    hooks["mnemonic"] = "top-level text should not win"

    storage, reason = prepare_hooks_for_storage(hooks)

    assert reason == "strong_candidate"
    assert storage is not None
    assert storage["mnemonic"] == "mnemonic 1"


def test_prepare_hooks_rejects_weak_candidate():
    hooks = _hooks([(5, 2, 5)])

    storage, reason = prepare_hooks_for_storage(hooks)

    assert storage is None
    assert reason == "weak_interaction"


def test_prepare_hooks_uses_best_index_not_first_candidate():
    hooks = _hooks([(5, 5, 5), (2, 2, 2)], best_index=1)

    storage, reason = prepare_hooks_for_storage(hooks)

    assert storage is None
    assert reason == "weak_sound_match_interaction_extraction"


def test_prepare_hooks_rejects_missing_candidate_scores():
    hooks = {
        "mnemonic": "sounds vaguely similar",
        "cognates": [],
        "collocations": [],
    }

    storage, reason = prepare_hooks_for_storage(hooks)

    assert storage is None
    assert reason == "missing_candidate_scores"


def test_direct_borrowing_is_accepted_as_a_real_hook():
    hooks = {
        "mnemonic": "This is a direct borrowing you already know.",
        "cognates": [
            {
                "lang": "Hindi",
                "word": "किताब (kitab)",
                "note": "direct borrowing — you already know this!",
            }
        ],
        "collocations": [],
    }

    ok, reason = hook_quality_reason(hooks)
    storage, storage_reason = prepare_hooks_for_storage(hooks)

    assert ok is True
    assert reason == "direct_borrowing"
    assert storage is not None
    assert storage_reason == "direct_borrowing"
