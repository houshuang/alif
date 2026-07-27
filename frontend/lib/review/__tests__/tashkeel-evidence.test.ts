import {
  EMPTY_TASHKEEL_INTERACTION,
  assistedRecognitionIndex,
  buildWordReviewEvidence,
  toggleFailureCause,
  toggleTashkeelInteraction,
} from "../tashkeel-evidence";

describe("assistedRecognitionIndex", () => {
  it("keeps the most recently tapped yellow word active after focus moves", () => {
    expect(
      assistedRecognitionIndex(new Set([1]), [1, 3], 3),
    ).toBe(1);
  });

  it("uses a newly focused yellow word when several are marked", () => {
    expect(
      assistedRecognitionIndex(new Set([1, 3]), [1, 3], 3),
    ).toBe(3);
  });

  it("falls back to a marked word after history is restored without taps", () => {
    expect(
      assistedRecognitionIndex(new Set([2]), [], null),
    ).toBe(2);
  });
});
import { SentenceWordMeta } from "../../types";

const words: SentenceWordMeta[] = [
  {
    sentence_word_id: 10,
    sentence_id: 1,
    position: 0,
    lemma_id: 100,
    surface_form: "كُتُب",
    gloss_en: "books",
    stability: 120,
    is_due: false,
    is_function_word: false,
    knowledge_state: "known",
    root: null,
    root_meaning: null,
    root_id: null,
    frequency_rank: null,
    cefr_level: null,
    show_tashkeel: false,
  },
  {
    sentence_word_id: 11,
    sentence_id: 1,
    position: 1,
    lemma_id: 101,
    surface_form: "قَلَم",
    gloss_en: "pen",
    stability: 10,
    is_due: true,
    is_function_word: false,
    knowledge_state: "learning",
    root: null,
    root_meaning: null,
    root_id: null,
    frequency_rank: null,
    cefr_level: null,
    show_tashkeel: true,
  },
];

describe("tashkeel review evidence", () => {
  it("records mixed initial visibility and exact token ratings", () => {
    const evidence = buildWordReviewEvidence({
      words,
      signal: "partial",
      missedIndices: new Set(),
      confusedIndices: new Set([0]),
      failureCausesByIndex: {
        0: ["unfamiliar_form", "missing_tashkeel"],
      },
      tashkeel: EMPTY_TASHKEEL_INTERACTION,
      answerRevealed: true,
    });

    expect(evidence).toHaveLength(2);
    expect(evidence[0]).toMatchObject({
      sentence_word_id: 10,
      rating: 2,
      rendered_front_form: "كتب",
      front_initial_tashkeel_visible: false,
      front_ever_tashkeel_visible: false,
      failure_causes: ["unfamiliar_form", "missing_tashkeel"],
    });
    expect(evidence[1]).toMatchObject({
      sentence_word_id: 11,
      rating: 3,
      rendered_front_form: "قَلَم",
      front_initial_tashkeel_visible: true,
      failure_causes: [],
    });
  });

  it("records a front reveal even if the learner toggles vowels off again", () => {
    const forced = toggleTashkeelInteraction(
      EMPTY_TASHKEEL_INTERACTION,
      "front",
    );
    const toggledBack = toggleTashkeelInteraction(forced, "front");
    const evidence = buildWordReviewEvidence({
      words: [words[0]],
      signal: "partial",
      missedIndices: new Set(),
      confusedIndices: new Set([0]),
      failureCausesByIndex: { 0: ["missing_tashkeel"] },
      tashkeel: toggledBack,
      answerRevealed: true,
    });

    expect(evidence[0]).toMatchObject({
      front_initial_tashkeel_visible: false,
      front_ever_tashkeel_visible: true,
      front_tashkeel_visible_at_answer: false,
      front_toggle_count: 2,
    });
  });

  it("records back-side hiding independently", () => {
    const backHidden = toggleTashkeelInteraction(
      EMPTY_TASHKEEL_INTERACTION,
      "back",
    );
    const evidence = buildWordReviewEvidence({
      words: [words[1]],
      signal: "understood",
      missedIndices: new Set(),
      confusedIndices: new Set(),
      failureCausesByIndex: {},
      tashkeel: backHidden,
      answerRevealed: true,
    });

    expect(evidence[0].back_tashkeel_visible_at_rating).toBe(false);
    expect(evidence[0].back_toggle_count).toBe(1);
  });

  it("drops missing-tashkeel causes when the stored token has no vowels", () => {
    const evidence = buildWordReviewEvidence({
      words: [{
        ...words[0],
        surface_form: "كتب",
        show_tashkeel: false,
      }],
      signal: "partial",
      missedIndices: new Set(),
      confusedIndices: new Set([0]),
      failureCausesByIndex: { 0: ["missing_tashkeel"] },
      tashkeel: EMPTY_TASHKEEL_INTERACTION,
      answerRevealed: true,
    });

    expect(evidence[0].failure_causes).toEqual([]);
  });
});

describe("failure cause selection", () => {
  it("makes retrieval lapse exclusive while allowing specific causes together", () => {
    expect(toggleFailureCause(["mixed_up"], "retrieval_lapse")).toEqual([
      "retrieval_lapse",
    ]);
    expect(toggleFailureCause(["retrieval_lapse"], "unfamiliar_form")).toEqual([
      "unfamiliar_form",
    ]);
    expect(toggleFailureCause(["unfamiliar_form"], "missing_tashkeel")).toEqual([
      "unfamiliar_form",
      "missing_tashkeel",
    ]);
  });
});
