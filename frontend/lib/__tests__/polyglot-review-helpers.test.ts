import {
  buildInterleavedSlots,
  cycleMark,
  deriveSignal,
  emptyMarks,
  hasAnyMarks,
  isContentWord,
  lemmaIdsFromMarks,
  markStateAt,
  middleButtonLabel,
  pageReviewClientId,
} from "../polyglot-review-helpers";
import type { IntroCard, SentencePayload, WordRender } from "../polyglot-api";

function word(overrides: Partial<WordRender> = {}): WordRender {
  return {
    position: 0,
    surface_form: "λόγος",
    lemma_id: 42,
    lemma_form: "λόγος",
    gloss_en: "word/reason",
    is_target: false,
    is_function_word: false,
    is_proper_name: false,
    knowledge_state: "new",
    ...overrides,
  };
}

describe("cycleMark", () => {
  it("walks off → missed → confused → off", () => {
    let m = emptyMarks();
    expect(markStateAt(m, 3)).toBe("off");

    m = cycleMark(m, 3);
    expect(markStateAt(m, 3)).toBe("missed");

    m = cycleMark(m, 3);
    expect(markStateAt(m, 3)).toBe("confused");

    m = cycleMark(m, 3);
    expect(markStateAt(m, 3)).toBe("off");
  });

  it("does not mutate the input MarkSets", () => {
    const before = emptyMarks();
    const after = cycleMark(before, 1);
    expect(before.missed.size).toBe(0);
    expect(after.missed.has(1)).toBe(true);
  });

  it("tracks marks per index independently", () => {
    let m = emptyMarks();
    m = cycleMark(m, 0);          // 0: missed
    m = cycleMark(m, 1);          // 1: missed
    m = cycleMark(m, 1);          // 1: confused
    expect(markStateAt(m, 0)).toBe("missed");
    expect(markStateAt(m, 1)).toBe("confused");
    expect(markStateAt(m, 2)).toBe("off");
  });
});

describe("deriveSignal + middleButtonLabel", () => {
  it("returns understood/Know All with no marks", () => {
    expect(deriveSignal(false)).toBe("understood");
    expect(middleButtonLabel(false)).toBe("Know All");
  });

  it("returns partial/Continue with any marks", () => {
    expect(deriveSignal(true)).toBe("partial");
    expect(middleButtonLabel(true)).toBe("Continue");
  });

  it("hasAnyMarks reflects either set being non-empty", () => {
    const empty = emptyMarks();
    expect(hasAnyMarks(empty)).toBe(false);

    const onlyMissed = cycleMark(empty, 0);
    expect(hasAnyMarks(onlyMissed)).toBe(true);

    const onlyConfused = cycleMark(cycleMark(empty, 0), 0);
    expect(hasAnyMarks(onlyConfused)).toBe(true);
  });
});

describe("isContentWord", () => {
  it("accepts ordinary content lemmas", () => {
    expect(isContentWord(word())).toBe(true);
  });

  it("rejects words with no lemma_id", () => {
    expect(isContentWord(word({ lemma_id: null }))).toBe(false);
  });

  it("rejects function words", () => {
    expect(isContentWord(word({ is_function_word: true }))).toBe(false);
  });

  it("rejects proper names", () => {
    expect(isContentWord(word({ is_proper_name: true }))).toBe(false);
  });
});

describe("lemmaIdsFromMarks", () => {
  const words: WordRender[] = [
    word({ position: 0, lemma_id: 10 }),                              // content
    word({ position: 1, lemma_id: 11, is_function_word: true }),      // function
    word({ position: 2, lemma_id: 12, is_proper_name: true }),        // proper name
    word({ position: 3, lemma_id: null }),                            // no lemma
    word({ position: 4, lemma_id: 14 }),                              // content
  ];

  it("filters function words and proper names out of missed/confused arrays", () => {
    let m = emptyMarks();
    m = cycleMark(m, 0);              // missed: content
    m = cycleMark(m, 1);              // missed: function word
    m = cycleMark(m, 2);              // missed: proper name
    m = cycleMark(m, 3);              // missed: no lemma
    m = cycleMark(m, 4); m = cycleMark(m, 4);  // confused: content

    const { missed, confused } = lemmaIdsFromMarks(m, words);
    expect(missed).toEqual([10]);
    expect(confused).toEqual([14]);
  });

  it("returns empty arrays when no marks", () => {
    const { missed, confused } = lemmaIdsFromMarks(emptyMarks(), words);
    expect(missed).toEqual([]);
    expect(confused).toEqual([]);
  });
});

describe("pageReviewClientId", () => {
  it("is deterministic per (story, page) so re-advancing dedups server-side", () => {
    expect(pageReviewClientId(7, 3)).toBe("pr:7:3");
    expect(pageReviewClientId(7, 3)).toBe(pageReviewClientId(7, 3));
    expect(pageReviewClientId(7, 4)).not.toBe(pageReviewClientId(7, 3));
    expect(pageReviewClientId(8, 3)).not.toBe(pageReviewClientId(7, 3));
  });
});

describe("buildInterleavedSlots", () => {
  function intro(lemmaId: number, lemmaForm: string, kind: "new" | "rescue" = "new"): IntroCard {
    return {
      lemma_id: lemmaId,
      lemma_form: lemmaForm,
      lemma_bare: lemmaForm,
      gloss_en: "gloss",
      pos: "noun",
      intro_kind: kind,
      times_seen: 0,
      cognate_lemma_id: null,
      cognate_lemma_form: null,
    };
  }
  function sent(id: number, lemmaIds: number[]): SentencePayload {
    return {
      sentence_id: id,
      text: lemmaIds.join(" "),
      translation_en: "t",
      target_lemma_id: lemmaIds[0],
      source: "test",
      page_id: null,
      words: lemmaIds.map((lid, i) => ({
        position: i,
        surface_form: String(lid),
        lemma_id: lid,
        lemma_form: String(lid),
        gloss_en: "g",
        is_target: i === 0,
        is_function_word: false,
        is_proper_name: false,
        knowledge_state: "acquiring",
      })),
      selection_reason: "test",
      score: 1.0,
    };
  }

  it("returns a flat sentence list when there are no intro cards", () => {
    const slots = buildInterleavedSlots([sent(1, [10]), sent(2, [20])], []);
    expect(slots).toEqual([
      { type: "sentence", sentenceIndex: 0 },
      { type: "sentence", sentenceIndex: 1 },
    ]);
  });

  it("emits an intro card before the sentence that contains its lemma", () => {
    const slots = buildInterleavedSlots(
      [sent(1, [10, 20]), sent(2, [20])],
      [intro(10, "α"), intro(20, "β")],
    );
    expect(slots).toEqual([
      { type: "intro", introIndex: 0 },
      { type: "intro", introIndex: 1 },
      { type: "sentence", sentenceIndex: 0 },
      { type: "sentence", sentenceIndex: 1 },
    ]);
  });

  it("does not re-emit an intro card whose lemma already appeared", () => {
    const slots = buildInterleavedSlots(
      [sent(1, [10]), sent(2, [10, 20]), sent(3, [10])],
      [intro(10, "α"), intro(20, "β")],
    );
    expect(slots).toEqual([
      { type: "intro", introIndex: 0 },
      { type: "sentence", sentenceIndex: 0 },
      { type: "intro", introIndex: 1 },
      { type: "sentence", sentenceIndex: 1 },
      { type: "sentence", sentenceIndex: 2 },
    ]);
  });

  it("flushes orphan intros (no covering sentence) at the front", () => {
    const slots = buildInterleavedSlots(
      [sent(1, [10])],
      [intro(10, "α"), intro(99, "orphan")],
    );
    expect(slots[0]).toEqual({ type: "intro", introIndex: 1 });
    expect(slots).toContainEqual({ type: "intro", introIndex: 0 });
    expect(slots).toContainEqual({ type: "sentence", sentenceIndex: 0 });
  });

  it("suppresses intros for lemmas already shown earlier in the UI session", () => {
    const slots = buildInterleavedSlots(
      [sent(1, [10, 20])],
      [intro(10, "α"), intro(20, "β")],
      new Set([10]),
    );
    expect(slots).toEqual([
      { type: "intro", introIndex: 1 },
      { type: "sentence", sentenceIndex: 0 },
    ]);
  });
});
