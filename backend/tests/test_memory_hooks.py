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


def test_prepare_hooks_accepts_v2_candidate_score_keys():
    """Round-2 generation uses cover/trigger/extraction; alias table must map them."""
    hooks = {
        "mnemonic": "placeholder",
        "candidates": [
            {"keyword": "HUT", "mnemonic": "A HUT surrounds you.", "cover": 5, "trigger": 4, "extraction": 5},
        ],
        "best_index": 0,
    }
    stored, reason = prepare_hooks_for_storage(hooks)
    assert stored is not None
    assert reason == "strong_candidate"
    assert stored["mnemonic"] == "A HUT surrounds you."


def test_judge_decision_rule_ignores_oddity(monkeypatch):
    """anchor+enacted+trigger store; memorable_oddity is diagnostic only."""
    from app.services import memory_hooks as mh

    monkeypatch.setattr(mh, "_call_hooks_llm", lambda *a, **k: {
        "known_anchor": True, "enacted_meaning": True, "automatic_trigger": True,
        "memorable_oddity": False, "storable": False, "reason": "plain scene",
    })
    storable, reason = mh.judge_memory_hook("word=x", "A HUT surrounds you.")
    assert storable is True
    assert reason.startswith("[not odd]")

    monkeypatch.setattr(mh, "_call_hooks_llm", lambda *a, **k: {
        "known_anchor": False, "enacted_meaning": True, "automatic_trigger": True,
        "memorable_oddity": True, "storable": False, "reason": "circular keyword",
    })
    storable, _ = mh.judge_memory_hook("word=x", "A KHADDAR binds your arm.")
    assert storable is False


def test_judge_unavailable_is_rejection(monkeypatch):
    """Verification failure is never success (project invariant)."""
    from app.services import memory_hooks as mh

    monkeypatch.setattr(mh, "_call_hooks_llm", lambda *a, **k: None)
    storable, reason = mh.judge_memory_hook("word=x", "anything")
    assert storable is False
    assert reason == "judge_unavailable"


def test_generate_judge_and_store_stamps_approval(monkeypatch):
    """Approved hooks get approved_at/approved_by; rejected hooks stored without."""
    from app.services import memory_hooks as mh

    class FakeLemma:
        lemma_id = 1
        lemma_ar = "بيت"
        lemma_ar_bare = "بيت"
        transliteration_ala_lc = "bayt"
        pos = "noun"
        gloss_en = "house"
        root = None
        etymology_json = None
        memory_hooks_json = None

    class FakeDB:
        committed = False
        def commit(self):
            self.committed = True

    gen_result = {
        "candidates": [
            {"keyword": "BAIT", "mnemonic": "A house made of BAIT.", "cover": 5, "trigger": 5, "extraction": 4},
        ],
        "best_index": 0,
        "mnemonic": "A house made of BAIT.",
        "cognates": [], "collocations": [], "usage_context": None, "fun_fact": None,
    }

    def fake_llm(prompt, system_prompt, schema, task_type):
        if task_type == "memory_hook_judge":
            return {"known_anchor": True, "enacted_meaning": True,
                    "automatic_trigger": True, "memorable_oddity": True,
                    "storable": True, "reason": "ok"}
        return dict(gen_result)

    monkeypatch.setattr(mh, "_call_hooks_llm", fake_llm)
    lemma, db = FakeLemma(), FakeDB()
    mh._generate_judge_and_store(db, lemma, task_type="memory_hooks")
    assert db.committed
    assert lemma.memory_hooks_json["approved_at"]
    assert lemma.memory_hooks_json["approved_by"].startswith("judge:")

    def fake_llm_reject(prompt, system_prompt, schema, task_type):
        if task_type == "memory_hook_judge":
            return {"known_anchor": False, "enacted_meaning": True,
                    "automatic_trigger": True, "memorable_oddity": True,
                    "storable": False, "reason": "circular"}
        return dict(gen_result)

    monkeypatch.setattr(mh, "_call_hooks_llm", fake_llm_reject)
    lemma2, db2 = FakeLemma(), FakeDB()
    mh._generate_judge_and_store(db2, lemma2, task_type="memory_hooks")
    assert db2.committed
    assert lemma2.memory_hooks_json["mnemonic"] == "A house made of BAIT."
    assert "approved_at" not in lemma2.memory_hooks_json
