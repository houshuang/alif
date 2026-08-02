import { BookPageToken } from "./types";

export function bookLookupDraftKey(storyId: number, pageNumber: number): string {
  return `@alif:book-reader:lookups:${storyId}:${pageNumber}`;
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
