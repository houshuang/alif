import {
  bookLookupDraftKey,
  bookPassageDraftKey,
  bookReaderLocationKey,
  groupBookTokens,
  normalizedBookSurface,
  parseBookPassageDraft,
  parseBookReaderLocation,
  parseBookLookupDraft,
  pendingBookLookupIds,
  positionsForSameUnmappedSurface,
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

  test("uses stable passage and exact-location storage keys", () => {
    expect(bookPassageDraftKey(4, 12, [31, 32])).toBe("@alif:book-reader:passage:4:12:31-32");
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
      dontLearnTokenPositions: [3, "4"],
      clientReviewId: "bp:stable",
    }))).toEqual({
      unknownLemmaIds: [9],
      unknownTokenPositions: [0, 2],
      dontLearnTokenPositions: [3],
      clientReviewId: "bp:stable",
    });
  });

  test("groups repeated unmapped surfaces so opt-out and admission cannot conflict", () => {
    const first = { ...token(0, 1), lemma_id: null, has_full_entry: false, surface_form: "وَكِتَابٌ،" };
    const second = { ...token(4, 1), lemma_id: null, has_full_entry: false, surface_form: "وكتابٌ." };
    const other = { ...token(5, 1), lemma_id: null, has_full_entry: false, surface_form: "بَيْتٌ." };
    expect(normalizedBookSurface(first.surface_form)).toBe("وكتاب");
    expect(positionsForSameUnmappedSurface([first, second, other], first)).toEqual([0, 4]);
  });
});
