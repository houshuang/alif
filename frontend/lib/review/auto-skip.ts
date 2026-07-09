export interface AutoSkipWord {
  lemma_id: number | null;
  canonical_lemma_id?: number | null;
  is_due?: boolean;
  knowledge_state?: string;
}

export interface AutoSkipOutcome {
  failed: boolean;
  canonical_lemma_id?: number | null;
}

/**
 * A sentence may cover several due lemmas even when only its primary lemma
 * caused an earlier card. Do not skip that sentence until every due obligation
 * has already received a successful outcome in the current session.
 */
export function canSkipDueObligations(
  dueLemmaIds: number[] | undefined,
  words: AutoSkipWord[],
  outcomes: Map<number, AutoSkipOutcome>,
): boolean {
  // Cached cards from before due_lemma_ids was added still carry word-level
  // is_due flags. Infer from those flags, and fail closed if neither form of
  // metadata can prove which obligations the card represents.
  const obligations = dueLemmaIds ?? Array.from(new Set(
    words
      .filter((word) => word.is_due)
      .map((word) => word.canonical_lemma_id ?? word.lemma_id)
      .filter((lemmaId): lemmaId is number => lemmaId != null),
  ));
  if (dueLemmaIds === undefined && obligations.length === 0) return false;
  if (obligations.length === 0) return true;

  return obligations.every((dueLemmaId) => {
    const matchingWords = words.filter(
      (word) =>
        word.lemma_id === dueLemmaId ||
        word.canonical_lemma_id === dueLemmaId,
    );

    // Acquisition repetitions are an encoding guarantee, not duplicate mature
    // reviews. Keep every planned card even after an earlier success.
    if (matchingWords.some((word) => word.knowledge_state === "acquiring")) {
      return false;
    }

    const relevantOutcomeIds = new Set<number>([dueLemmaId]);
    for (const word of matchingWords) {
      if (word.lemma_id != null) relevantOutcomeIds.add(word.lemma_id);
      if (word.canonical_lemma_id != null) {
        relevantOutcomeIds.add(word.canonical_lemma_id);
      }
    }

    const relevantOutcomes: AutoSkipOutcome[] = [];
    for (const [outcomeLemmaId, outcome] of outcomes) {
      if (
        relevantOutcomeIds.has(outcomeLemmaId) ||
        outcome.canonical_lemma_id === dueLemmaId
      ) {
        relevantOutcomes.push(outcome);
      }
    }

    return (
      relevantOutcomes.length > 0 &&
      relevantOutcomes.every((outcome) => !outcome.failed)
    );
  });
}
