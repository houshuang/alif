import {
  ACTIVE_BOOK_READER_KEY,
  bookLookupDraftKey,
  bookPassageDraftKey,
  bookReaderLocationKey,
  bookReaderModeKey,
  countGuidedLearningWords,
  groupBookTokens,
  isBookTokenMarked,
  normalizedBookSurface,
  parseBookPassageDraft,
  parseActiveBookReader,
  parseBookReaderLocation,
  parseBookLookupDraft,
  pendingBookLookupIds,
  positionsForSameGuidedWord,
  positionsForSameUnmappedSurface,
  sameBookToken,
  shortBookGloss,
} from "../book-reader";
import { BookPageToken } from "../types";

const token = (position: number, sentenceIndex: number): BookPageToken => ({
  position,
  sentence_index: sentenceIndex,
  surface_form: `word-${position}`,
  lemma_id: position + 1,
  gloss_en: null,
  knowledge_state: null,
  acquisition_box: null,
  stability: null,
  show_tashkeel: true,
  is_function_word: false,
  is_proper_name: false,
  is_schedulable: true,
  has_full_entry: true,
  reader_gloss_eligible: true,
});

describe("book reader draft helpers", () => {
  test("uses a stable per-page storage key", () => {
    expect(bookLookupDraftKey(4, 12)).toBe("@alif:book-reader:lookups:4:12");
  });

  test("keeps the active book route and per-policy display preferences stable", () => {
    expect(ACTIVE_BOOK_READER_KEY).toBe("@alif:book-reader:active");
    expect(bookReaderModeKey("clean")).toBe("@alif:book-reader:mode:clean");
    expect(bookReaderModeKey("guided")).toBe("@alif:book-reader:mode:guided");
    expect(parseActiveBookReader('{"storyId":240,"pageNumber":2,"active":true}')).toEqual({
      storyId: 240,
      pageNumber: 2,
      active: true,
    });
    expect(parseActiveBookReader('{"storyId":0,"pageNumber":2,"active":true}')).toBeNull();
  });

  test("parses only unique positive integer lemma ids", () => {
    expect(parseBookLookupDraft('[5, 5, -1, "6", 7.5, 8]')).toEqual([5, 8]);
    expect(parseBookLookupDraft("not-json")).toEqual([]);
  });

  test("persists only lookups not already recorded by the server", () => {
    expect(pendingBookLookupIds([2, 3, 4, 4], [2, 4])).toEqual([3]);
  });

  test("keeps sentence boundaries while preserving token order", () => {
    const groups = groupBookTokens([token(0, 1), token(1, 1), token(2, 2)]);
    expect(groups.map((group) => group.map((item) => item.position))).toEqual([[0, 1], [2]]);
  });

  test("uses stable passage and exact-location storage keys", () => {
    expect(bookPassageDraftKey(4, 12, [31, 32])).toBe("@alif:book-reader:passage:4:12:31-32:clean");
    expect(bookPassageDraftKey(4, 12, [31, 32], "guided")).toBe("@alif:book-reader:passage:4:12:31-32:guided");
    expect(bookReaderLocationKey(4)).toBe("@alif:book-reader:location:4");
    expect(parseBookReaderLocation('{"pageNumber":12,"sentenceIndex":31}')).toEqual({
      pageNumber: 12,
      sentenceIndex: 31,
    });
  });

  test("restores replay-safe passage evidence and filters malformed positions", () => {
    expect(parseBookPassageDraft(JSON.stringify({
      unknownLemmaIds: [9, 9, -1],
      unknownTokenPositions: [0, 2, 2, 3, -1],
      markedTokenPositions: [2, 2, 7, -1],
      dontLearnTokenPositions: [3, "4"],
      learnTokenPositions: [5, 5, -2],
      clientReviewId: "bp:stable",
    }))).toEqual({
      unknownLemmaIds: [9],
      unknownTokenPositions: [0, 2],
      markedTokenPositions: [2, 7],
      dontLearnTokenPositions: [3],
      learnTokenPositions: [5],
      clientReviewId: "bp:stable",
    });
  });

  test("groups repeated unmapped surfaces for Clean-reader decisions", () => {
    const first = { ...token(0, 1), lemma_id: null, has_full_entry: false, surface_form: "وَكِتَابٌ،" };
    const second = { ...token(4, 1), lemma_id: null, has_full_entry: false, surface_form: "وكتابٌ." };
    const other = { ...token(5, 1), lemma_id: null, has_full_entry: false, surface_form: "بَيْتٌ." };
    expect(normalizedBookSurface(first.surface_form)).toBe("وكتاب");
    expect(positionsForSameUnmappedSurface([first, second, other], first)).toEqual([0, 4]);
    expect(positionsForSameGuidedWord([first, second, other], first)).toEqual([0]);
  });

  test("keeps guided selection on the exact tapped token, even for the same lemma", () => {
    const first = { ...token(0, 1), lemma_id: 7, surface_form: "والسادة" };
    const otherInflection = { ...token(4, 2), lemma_id: 7, surface_form: "وسادتي" };
    expect(positionsForSameGuidedWord([first, otherInflection], first)).toEqual([0]);
    expect(sameBookToken(first, first)).toBe(true);
    expect(sameBookToken(first, otherInflection)).toBe(false);
    expect(sameBookToken(null, first)).toBe(false);
  });

  test("paints only the exact missed token while retaining a lemma-level miss", () => {
    const tapped = { ...token(15, 1), lemma_id: 267, surface_form: "شخصًا" };
    const otherForm = { ...token(33, 1), lemma_id: 267, surface_form: "شخص" };
    const draft = parseBookPassageDraft(JSON.stringify({
      unknownLemmaIds: [267],
      unknownTokenPositions: [],
      markedTokenPositions: [15],
      dontLearnTokenPositions: [],
      learnTokenPositions: [],
      clientReviewId: "bp:exact-token",
    }));
    expect(isBookTokenMarked(draft, tapped)).toBe(true);
    expect(isBookTokenMarked(draft, otherForm)).toBe(false);
    expect(draft?.unknownLemmaIds).toEqual([267]);
  });

  test("keeps inline glosses compact and reader-like", () => {
    expect(shortBookGloss("to become; to turn into")).toBe("to become");
    expect(shortBookGloss("an unusually long contextual explanation")).toBe("an unusually…");
    expect(shortBookGloss(null)).toBeNull();
  });

  test("counts repeated guided selections as one word", () => {
    const first = { ...token(0, 0), lemma_id: 7 };
    const repeat = { ...token(4, 1), lemma_id: 7 };
    const unmapped = { ...token(5, 1), lemma_id: null, surface_form: "بَيْتٌ." };
    expect(countGuidedLearningWords([first, repeat, unmapped], [0, 4, 5])).toBe(2);
  });
});
