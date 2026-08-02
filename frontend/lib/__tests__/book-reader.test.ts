import {
  bookLookupDraftKey,
  groupBookTokens,
  parseBookLookupDraft,
  pendingBookLookupIds,
} from "../book-reader";
import { BookPageToken } from "../types";

const token = (position: number, sentenceIndex: number): BookPageToken => ({
  position,
  sentence_index: sentenceIndex,
  surface_form: `word-${position}`,
  lemma_id: position + 1,
  gloss_en: null,
  knowledge_state: null,
  is_function_word: false,
  is_proper_name: false,
  is_schedulable: true,
});

describe("book reader draft helpers", () => {
  test("uses a stable per-page storage key", () => {
    expect(bookLookupDraftKey(4, 12)).toBe("@alif:book-reader:lookups:4:12");
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
});
