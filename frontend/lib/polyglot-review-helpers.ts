/**
 * Pure helpers for the polyglot sentence-review screen.
 *
 * Extracted so the mark cycle and signal derivation are unit-testable without
 * spinning up React Native. Mirrors the equivalent logic in Alif's
 * `frontend/app/index.tsx` (`toggleMissed` at line ~814, `handleSentenceSubmit`
 * at line ~1143) — see `polyglot/CLAUDE.md` § "Ground design and code in Alif".
 */
import type { ComprehensionSignal, IntroCard, SentencePayload, WordRender } from "./polyglot-api";

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

/**
 * Slot type for the interleaved review session.
 *
 * Polyglot has just two card types today: intro cards and sentence cards.
 * Alif's `buildInterleavedSession` (frontend/app/index.tsx:99-198) also
 * handles passages, verses, and deprecated intro candidates — none of which
 * polyglot emits yet, so the logic here is the trimmed core: emit an unshown
 * intro card before the sentence whose target lemma it covers. Orphan intros
 * (whose target lemma isn't in any sentence) are flushed at the front so the
 * learner never gets a card for a word they won't then see in context.
 */
export type SessionSlot =
  | { type: "intro"; introIndex: number }
  | { type: "sentence"; sentenceIndex: number };

/**
 * Interleave intro cards before their target sentence so the learner never
 * sees a content word before its introduction. Mirrors Alif's pattern but
 * drops the wind-down + warm-up reshuffling — polyglot sessions are short
 * enough today that linear order works fine; revisit when sessions grow.
 *
 * `alreadyShownLemmaIds` lets the caller suppress intros for lemmas already
 * displayed in the current UI session, even if the server hasn't observed
 * the ack yet. This prevents an intro from re-firing while a network request
 * is in flight.
 */
export function buildInterleavedSlots(
  sentences: readonly SentencePayload[],
  introCards: readonly IntroCard[],
  alreadyShownLemmaIds: ReadonlySet<number> = new Set(),
): SessionSlot[] {
  // `introIndex` is an index into the original `introCards` array so the
  // consumer can do `bundle.intro_cards[slot.introIndex]` directly. Filtering
  // for already-shown cards happens by skipping at slot-emit time rather than
  // by reshaping the array, preserving that contract.
  const lemmaToIntroIdx = new Map<number, number>();
  introCards.forEach((c, i) => {
    if (!alreadyShownLemmaIds.has(c.lemma_id)) {
      lemmaToIntroIdx.set(c.lemma_id, i);
    }
  });

  if (lemmaToIntroIdx.size === 0) {
    return sentences.map((_, i) => ({ type: "sentence", sentenceIndex: i }));
  }

  const slots: SessionSlot[] = [];
  const shown = new Set<number>();

  for (let si = 0; si < sentences.length; si++) {
    const sentence = sentences[si];
    for (const word of sentence.words) {
      if (word.lemma_id == null) continue;
      const introIdx = lemmaToIntroIdx.get(word.lemma_id);
      if (introIdx !== undefined && !shown.has(word.lemma_id)) {
        slots.push({ type: "intro", introIndex: introIdx });
        shown.add(word.lemma_id);
      }
    }
    slots.push({ type: "sentence", sentenceIndex: si });
  }

  // Orphan intros: lemma never appeared in any sentence (e.g. proper-name
  // collateral that the picker dropped). Flush at the front so the cards
  // still get a chance to land before the learner finishes the session.
  const orphanIntros: SessionSlot[] = [];
  introCards.forEach((c, i) => {
    if (alreadyShownLemmaIds.has(c.lemma_id)) return;
    if (!shown.has(c.lemma_id)) {
      orphanIntros.push({ type: "intro", introIndex: i });
    }
  });
  return [...orphanIntros, ...slots];
}

export function generateClientReviewId(): string {
  return `prv-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

export function generateSessionId(): string {
  return `psess-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}
