from app.services.sentence_generator import group_words_for_multi_target


def test_group_words_for_multi_target_is_demand_weighted_and_root_safe():
    words = [
        {"lemma_id": 1, "lemma_ar": "كِتَاب", "gloss_en": "book", "root_id": 10, "tier": 2, "due_str": "2026-05-02", "existing": 1, "needed": 1},
        {"lemma_id": 2, "lemma_ar": "كَاتِب", "gloss_en": "writer", "root_id": 10, "tier": 1, "due_str": "2026-05-01", "existing": 0, "needed": 2},
        {"lemma_id": 3, "lemma_ar": "قَلَم", "gloss_en": "pen", "root_id": 20, "tier": 1, "due_str": "2026-05-01", "existing": 0, "needed": 2},
        {"lemma_id": 4, "lemma_ar": "بَيْت", "gloss_en": "house", "root_id": 30, "tier": 1, "due_str": "2026-05-01", "existing": 0, "needed": 1},
    ]

    groups = group_words_for_multi_target(words, max_group_size=3)

    assert groups
    first_ids = {w["lemma_id"] for w in groups[0]}
    assert 2 in first_ids
    assert 3 in first_ids
    for group in groups:
        roots = [w["root_id"] for w in group if w.get("root_id") is not None]
        assert len(roots) == len(set(roots))


def test_group_words_for_multi_target_drops_ungroupable_seed():
    words = [
        {"lemma_id": 1, "lemma_ar": "كِتَاب", "root_id": 10, "tier": 1},
        {"lemma_id": 2, "lemma_ar": "كَاتِب", "root_id": 10, "tier": 1},
    ]

    assert group_words_for_multi_target(words, max_group_size=3) == []
