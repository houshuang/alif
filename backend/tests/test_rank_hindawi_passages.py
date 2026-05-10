from collections import Counter

from scripts.rank_hindawi_passages import (
    LemmaContext,
    LemmaInfo,
    PassageWindow,
    SentenceCoverage,
    build_windows,
    window_to_dict,
)


def test_passage_window_metrics_and_serialization():
    context = LemmaContext(
        infos={
            1: LemmaInfo(
                lemma_id=1,
                arabic="لَمْ",
                bare="لم",
                gloss="did not",
                pos="particle",
                rank=26,
            ),
            2: LemmaInfo(
                lemma_id=2,
                arabic="جَرَى",
                bare="جرى",
                gloss="to run",
                pos="verb",
                state="encountered",
            ),
        },
        states={1: "new", 2: "encountered"},
    )
    sentences = [
        SentenceCoverage(
            text="sentence one",
            content_tokens=5,
            known_tokens=3,
            active_tokens=4,
            missing=Counter({1: 1}),
        ),
        SentenceCoverage(
            text="sentence two",
            content_tokens=5,
            known_tokens=4,
            active_tokens=4,
            missing=Counter({2: 1}),
        ),
        SentenceCoverage(
            text="sentence three",
            content_tokens=5,
            known_tokens=4,
            active_tokens=4,
            unmapped=Counter({"الرداء": 1}),
        ),
    ]

    window = PassageWindow(
        title="لَيْلَى وَالذِّئْبُ",
        author="كامل كيلاني",
        start_index=7,
        sentences=sentences,
    )

    data = window_to_dict(window, context, include_text=True)

    assert data["start_sentence"] == 8
    assert data["content_tokens"] == 15
    assert data["active_pct"] == 80.0
    assert data["mapped_ceiling_pct"] == 93.3
    assert data["after_top_10_mapped_pct"] == 93.3
    assert data["top_missing"][0].startswith("#1 لَمْ")
    assert data["top_unmapped"] == ["الرداء x1"]
    assert data["sentences"] == ["sentence one", "sentence two", "sentence three"]


def test_build_windows_keeps_consecutive_sentence_offsets():
    sentences = [
        SentenceCoverage(str(i), 3, 3, 3)
        for i in range(5)
    ]

    windows = list(build_windows("title", "author", sentences, sentence_count=3))

    assert [w.start_index for w in windows] == [0, 1, 2]
    assert [[s.text for s in w.sentences] for w in windows] == [
        ["0", "1", "2"],
        ["1", "2", "3"],
        ["2", "3", "4"],
    ]
