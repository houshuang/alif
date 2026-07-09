import { canSkipDueObligations } from "../review/auto-skip";

describe("canSkipDueObligations", () => {
  const words = [
    { lemma_id: 10, canonical_lemma_id: null },
    { lemma_id: 21, canonical_lemma_id: 20 },
    { lemma_id: 30, canonical_lemma_id: null },
  ];

  it("allows a card whose explicit due-obligation list is empty", () => {
    expect(canSkipDueObligations([], words, new Map())).toBe(true);
  });

  it("fails closed when a legacy card has no due metadata", () => {
    expect(canSkipDueObligations(undefined, words, new Map())).toBe(false);
  });

  it("infers legacy due obligations from word-level flags", () => {
    const legacyWords = [
      { lemma_id: 10, canonical_lemma_id: null, is_due: true },
      { lemma_id: 30, canonical_lemma_id: null, is_due: false },
    ];
    const outcomes = new Map([[10, { failed: false }]]);

    expect(canSkipDueObligations(undefined, legacyWords, outcomes)).toBe(true);
  });

  it("requires a successful outcome for every due lemma", () => {
    const outcomes = new Map([
      [10, { failed: false }],
      [30, { failed: false }],
    ]);

    expect(canSkipDueObligations([10, 30], words, outcomes)).toBe(true);
    expect(canSkipDueObligations([10, 30, 40], words, outcomes)).toBe(false);
  });

  it("does not treat a failed due lemma as covered", () => {
    const outcomes = new Map([
      [10, { failed: false }],
      [30, { failed: true }],
    ]);

    expect(canSkipDueObligations([10, 30], words, outcomes)).toBe(false);
  });

  it("maps a canonical due lemma to its successful surface outcome", () => {
    const outcomes = new Map([[21, { failed: false }]]);

    expect(canSkipDueObligations([20], words, outcomes)).toBe(true);
  });

  it("keeps a canonical due lemma when its surface outcome failed", () => {
    const outcomes = new Map([[21, { failed: true }]]);

    expect(canSkipDueObligations([20], words, outcomes)).toBe(false);
  });

  it("lets a later surface failure override an earlier canonical success", () => {
    const outcomes = new Map([
      [20, { failed: false, canonical_lemma_id: null }],
      [22, { failed: true, canonical_lemma_id: 20 }],
    ]);

    expect(canSkipDueObligations([20], words, outcomes)).toBe(false);
  });

  it("never skips a planned acquisition exposure", () => {
    const acquisitionWords = [
      { lemma_id: 10, canonical_lemma_id: null, knowledge_state: "known" },
      { lemma_id: 30, canonical_lemma_id: null, knowledge_state: "acquiring" },
    ];
    const outcomes = new Map([
      [10, { failed: false }],
      [30, { failed: false }],
    ]);

    expect(
      canSkipDueObligations([10, 30], acquisitionWords, outcomes),
    ).toBe(false);
  });
});
