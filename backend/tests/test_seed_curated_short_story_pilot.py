from scripts.seed_curated_short_story_pilot import CANDIDATES


def test_curated_pilot_has_three_disjoint_target_triples():
    groups = [candidate["selected_target_lemma_ids"] for candidate in CANDIDATES]

    assert all(len(group) == 3 and len(set(group)) == 3 for group in groups)
    assert len({lemma_id for group in groups for lemma_id in group}) == 9


def test_curated_pilot_candidates_match_embedded_story_contract():
    for candidate in CANDIDATES:
        assert len(candidate["sentences"]) == 4
        assert all(sentence["arabic"] and sentence["english"] for sentence in candidate["sentences"])
        assert candidate["narrative_mode"]
        assert candidate["premise"]
        assert candidate["target_plan"]

    morphology = [candidate for candidate in CANDIDATES if candidate["morphology_focus"]]
    assert len(morphology) == 1
    assert morphology[0]["morphology_target_lemma_id"] in morphology[0][
        "selected_target_lemma_ids"
    ]
