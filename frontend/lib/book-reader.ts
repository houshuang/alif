import { BookPageToken } from "./types";

export type BookReaderMode = "arabic" | "both";
export type BookReaderPolicy = "clean" | "guided";

export interface BookPassageDraft {
  unknownLemmaIds: number[];
  unknownTokenPositions: number[];
  dontLearnTokenPositions: number[];
  learnTokenPositions: number[];
  clientReviewId: string;
}

export interface BookReaderLocation {
  pageNumber: number;
  sentenceIndex: number;
  tokenPosition?: number;
}

export function bookLookupDraftKey(storyId: number, pageNumber: number): string {
  return `@alif:book-reader:lookups:${storyId}:${pageNumber}`;
}

export function bookPassageDraftKey(
  storyId: number,
  pageNumber: number,
  tokenPositions: number[],
  policy: BookReaderPolicy = "clean",
): string {
  const range = tokenPositions.length > 0
    ? `${tokenPositions[0]}-${tokenPositions[tokenPositions.length - 1]}`
    : "empty";
  return `@alif:book-reader:passage:${storyId}:${pageNumber}:${range}:${policy}`;
}

export function bookReaderLocationKey(storyId: number): string {
  return `@alif:book-reader:location:${storyId}`;
}

export function parseBookPassageDraft(raw: string | null): BookPassageDraft | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<BookPassageDraft>;
    if (!value || typeof value.clientReviewId !== "string" || !value.clientReviewId) return null;
    const integers = (items: unknown) => Array.isArray(items)
      ? Array.from(new Set(items.filter((item): item is number => Number.isInteger(item) && item >= 0)))
      : [];
    const dontLearnTokenPositions = integers(value.dontLearnTokenPositions);
    const dontLearn = new Set(dontLearnTokenPositions);
    return {
      unknownLemmaIds: integers(value.unknownLemmaIds),
      unknownTokenPositions: integers(value.unknownTokenPositions)
        .filter((position) => !dontLearn.has(position)),
      dontLearnTokenPositions,
      learnTokenPositions: integers(value.learnTokenPositions),
      clientReviewId: value.clientReviewId,
    };
  } catch {
    return null;
  }
}

export function shortBookGloss(gloss: string | null): string | null {
  if (!gloss) return null;
  const concise = gloss
    .replace(/^\(name\)\s*/i, "")
    .split(/[;|]/, 1)[0]
    .trim();
  if (!concise) return null;
  return concise.length <= 24 ? concise : `${concise.slice(0, 22).trimEnd()}…`;
}

export function parseBookReaderLocation(raw: string | null): BookReaderLocation | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<BookReaderLocation>;
    if (!Number.isInteger(value.pageNumber) || !Number.isInteger(value.sentenceIndex)) return null;
    return {
      pageNumber: value.pageNumber!,
      sentenceIndex: value.sentenceIndex!,
      tokenPosition: Number.isInteger(value.tokenPosition) ? value.tokenPosition : undefined,
    };
  } catch {
    return null;
  }
}

export function normalizedBookSurface(surface: string): string {
  return surface
    .replace(/[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]/g, "")
    .replace(/[،؛؟«»…]/g, "")
    .replace(/[^\u0600-\u06FF]/g, "")
    .replace(/[أإآٱ]/g, "ا");
}

export function positionsForSameUnmappedSurface(
  tokens: BookPageToken[],
  selected: BookPageToken,
): number[] {
  if (selected.lemma_id != null) return [selected.position];
  const key = normalizedBookSurface(selected.surface_form);
  return tokens
    .filter((token) => token.lemma_id == null && normalizedBookSurface(token.surface_form) === key)
    .map((token) => token.position);
}

export function positionsForSameGuidedWord(
  tokens: BookPageToken[],
  selected: BookPageToken,
): number[] {
  if (selected.lemma_id != null) {
    return tokens
      .filter((token) => (
        token.reader_gloss_eligible && token.lemma_id === selected.lemma_id
      ))
      .map((token) => token.position);
  }
  const key = normalizedBookSurface(selected.surface_form);
  return tokens
    .filter((token) => (
      token.reader_gloss_eligible
      && token.lemma_id == null
      && normalizedBookSurface(token.surface_form) === key
    ))
    .map((token) => token.position);
}

export function countGuidedLearningWords(
  tokens: BookPageToken[],
  selectedPositions: Iterable<number>,
): number {
  const positions = new Set(selectedPositions);
  const words = new Set<string>();
  for (const token of tokens) {
    if (!positions.has(token.position)) continue;
    const key = token.lemma_id != null
      ? `lemma:${token.lemma_id}`
      : `surface:${normalizedBookSurface(token.surface_form)}`;
    words.add(key);
  }
  return words.size;
}

export function parseBookLookupDraft(raw: string | null): number[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    return Array.from(new Set(
      value.filter((item): item is number => Number.isInteger(item) && item > 0),
    ));
  } catch {
    return [];
  }
}

export function pendingBookLookupIds(
  lookedUp: Iterable<number>,
  recorded: Iterable<number>,
): number[] {
  const recordedSet = new Set(recorded);
  return Array.from(new Set(lookedUp)).filter((lemmaId) => !recordedSet.has(lemmaId));
}

export function groupBookTokens(tokens: BookPageToken[]): BookPageToken[][] {
  const groups: BookPageToken[][] = [];
  let currentIndex: number | null | undefined;
  for (const token of tokens) {
    if (groups.length === 0 || token.sentence_index !== currentIndex) {
      groups.push([]);
      currentIndex = token.sentence_index;
    }
    groups[groups.length - 1].push(token);
  }
  return groups;
}
