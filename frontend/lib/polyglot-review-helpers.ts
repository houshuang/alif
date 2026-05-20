/**
 * Pure helpers for the polyglot sentence-review screen.
 *
 * Extracted so the mark cycle and signal derivation are unit-testable without
 * spinning up React Native. Mirrors the equivalent logic in Alif's
 * `frontend/app/index.tsx` (`toggleMissed` at line ~814, `handleSentenceSubmit`
 * at line ~1143) — see `polyglot/CLAUDE.md` § "Ground design and code in Alif".
 */
import type { ComprehensionSignal, WordRender } from "./polyglot-api";

export type MarkState = "off" | "missed" | "confused";

export type MarkSets = {
  missed: Set<number>;
  confused: Set<number>;
};

export function emptyMarks(): MarkSets {
  return { missed: new Set<number>(), confused: new Set<number>() };
}

export function markStateAt(marks: MarkSets, index: number): MarkState {
  if (marks.missed.has(index)) return "missed";
  if (marks.confused.has(index)) return "confused";
  return "off";
}

/**
 * Triple-tap cycle: off → missed → confused → off.
 *
 * Returns a new MarkSets — the caller should swap state, not mutate in place.
 */
export function cycleMark(marks: MarkSets, index: number): MarkSets {
  const missed = new Set(marks.missed);
  const confused = new Set(marks.confused);
  if (!missed.has(index) && !confused.has(index)) {
    missed.add(index);
  } else if (missed.has(index)) {
    missed.delete(index);
    confused.add(index);
  } else {
    confused.delete(index);
  }
  return { missed, confused };
}

export function hasAnyMarks(marks: MarkSets): boolean {
  return marks.missed.size > 0 || marks.confused.size > 0;
}

/**
 * A word is "tappable for marking" iff it is a content lemma — content words
 * earn FSRS credit, function words and proper names do not. Words without a
 * lemma_id (NULL surface forms) are not markable either.
 */
export function isContentWord(word: WordRender): boolean {
  if (word.lemma_id == null) return false;
  if (word.is_function_word) return false;
  if (word.is_proper_name) return false;
  return true;
}

/**
 * Derive the comprehension signal from the middle action button.
 *
 * Mirrors Alif's ReadingActions: the middle button is "Know All" when there
 * are no marks (→ understood) and "Continue" when any words are marked
 * (→ partial). The "No idea" button always sends no_idea independent of marks.
 */
export function deriveSignal(hasMarks: boolean): ComprehensionSignal {
  return hasMarks ? "partial" : "understood";
}

export function middleButtonLabel(hasMarks: boolean): "Know All" | "Continue" {
  return hasMarks ? "Continue" : "Know All";
}

/**
 * Build the missed/confused lemma_id arrays for the submit payload.
 *
 * Function words and proper names are filtered out — even if the user managed
 * to tap one, it shouldn't earn FSRS credit. Same rule as
 * sentence_review_service skips them on the backend; doing it client-side too
 * keeps the payload clean and the intent visible.
 */
export function lemmaIdsFromMarks(
  marks: MarkSets,
  words: readonly WordRender[],
): { missed: number[]; confused: number[] } {
  const missed: number[] = [];
  const confused: number[] = [];
  for (const idx of marks.missed) {
    const w = words[idx];
    if (w && w.lemma_id != null && isContentWord(w)) missed.push(w.lemma_id);
  }
  for (const idx of marks.confused) {
    const w = words[idx];
    if (w && w.lemma_id != null && isContentWord(w)) confused.push(w.lemma_id);
  }
  return { missed, confused };
}

export function generateClientReviewId(): string {
  return `prv-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

export function generateSessionId(): string {
  return `psess-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}
