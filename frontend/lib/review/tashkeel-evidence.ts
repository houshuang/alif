import {
  ComprehensionSignal,
  SentenceWordMeta,
  WordFailureCause,
  WordReviewEvidenceIn,
} from "../types";

export const WORD_REVIEW_EVIDENCE_PROTOCOL_VERSION = 2;

export interface TashkeelCardInteraction {
  frontOverride: boolean;
  backOverride: boolean;
  frontToggleCount: number;
  backToggleCount: number;
  frontEverForced: boolean;
}

export const EMPTY_TASHKEEL_INTERACTION: TashkeelCardInteraction = {
  frontOverride: false,
  backOverride: false,
  frontToggleCount: 0,
  backToggleCount: 0,
  frontEverForced: false,
};

export function stripDiacritics(s: string): string {
  return s.replace(/[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]/g, "");
}

export function hasTashkeel(s: string): boolean {
  return stripDiacritics(s) !== s;
}

export function toggleTashkeelInteraction(
  current: TashkeelCardInteraction,
  side: "front" | "back",
): TashkeelCardInteraction {
  if (side === "front") {
    const nextOverride = !current.frontOverride;
    return {
      ...current,
      frontOverride: nextOverride,
      frontToggleCount: current.frontToggleCount + 1,
      frontEverForced: current.frontEverForced || nextOverride,
    };
  }
  return {
    ...current,
    backOverride: !current.backOverride,
    backToggleCount: current.backToggleCount + 1,
  };
}

export function toggleFailureCause(
  current: readonly WordFailureCause[],
  cause: WordFailureCause,
): WordFailureCause[] {
  if (current.includes(cause)) {
    return current.filter((value) => value !== cause);
  }
  if (cause === "retrieval_lapse") {
    return ["retrieval_lapse"];
  }
  return [
    ...current.filter((value) => value !== "retrieval_lapse"),
    cause,
  ];
}

export function assistedRecognitionIndex(
  confusedIndices: ReadonlySet<number>,
  tappedOrder: readonly number[],
  focusedIndex: number | null,
): number | null {
  if (focusedIndex != null && confusedIndices.has(focusedIndex)) {
    return focusedIndex;
  }
  for (let i = tappedOrder.length - 1; i >= 0; i -= 1) {
    const index = tappedOrder[i];
    if (confusedIndices.has(index)) return index;
  }
  const marked = Array.from(confusedIndices);
  return marked.length > 0 ? marked[marked.length - 1] : null;
}

export function buildWordReviewEvidence({
  words,
  signal,
  missedIndices,
  confusedIndices,
  failureCausesByIndex,
  tashkeel,
  answerRevealed,
}: {
  words: SentenceWordMeta[];
  signal: ComprehensionSignal;
  missedIndices: ReadonlySet<number>;
  confusedIndices: ReadonlySet<number>;
  failureCausesByIndex: Record<number, WordFailureCause[]>;
  tashkeel: TashkeelCardInteraction;
  answerRevealed: boolean;
}): WordReviewEvidenceIn[] {
  const evidence: WordReviewEvidenceIn[] = [];

  words.forEach((word, index) => {
    if (
      word.sentence_word_id == null
      || word.lemma_id == null
    ) {
      return;
    }

    const defaultShowTashkeel = word.show_tashkeel !== false;
    const renderedFrontForm = defaultShowTashkeel
      ? word.surface_form
      : stripDiacritics(word.surface_form);
    const storedHasTashkeel = hasTashkeel(word.surface_form);
    const frontInitialVisible = hasTashkeel(renderedFrontForm);
    const frontEverVisible = (
      frontInitialVisible
      || (tashkeel.frontEverForced && storedHasTashkeel)
    );
    const frontVisibleAtAnswer = (
      storedHasTashkeel
      && (defaultShowTashkeel || tashkeel.frontOverride)
    );

    let rating: 1 | 2 | 3 = 3;
    if (signal === "no_idea" || missedIndices.has(index)) {
      rating = 1;
    } else if (confusedIndices.has(index)) {
      rating = 2;
    }

    const selectedCauses = rating === 2
      ? (failureCausesByIndex[index] ?? []).filter(
          (cause) => (
            cause !== "missing_tashkeel"
            || (storedHasTashkeel && !frontInitialVisible)
          ),
        )
      : [];

    evidence.push({
      sentence_word_id: word.sentence_word_id,
      rating,
      surface_form: word.surface_form,
      rendered_front_form: renderedFrontForm,
      default_show_tashkeel: defaultShowTashkeel,
      front_initial_tashkeel_visible: frontInitialVisible,
      front_ever_tashkeel_visible: frontEverVisible,
      front_tashkeel_visible_at_answer: frontVisibleAtAnswer,
      front_toggle_count: tashkeel.frontToggleCount,
      answer_revealed: answerRevealed,
      back_tashkeel_visible_at_rating: answerRevealed
        ? storedHasTashkeel && !tashkeel.backOverride
        : null,
      back_toggle_count: tashkeel.backToggleCount,
      failure_causes: selectedCauses,
    });
  });

  return evidence;
}
