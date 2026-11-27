import { ConfusionAnalysis } from "../types";

export interface SuggestedWord { lemma_id: number; lemma_ar: string; gloss_en: string | null }

/** Words to offer as "mixed up with": exact sound-alikes (سعد/صعد) first,
 * then look-alikes, then near sound-alikes. */
export function confusionSuggestions(data: ConfusionAnalysis | null, excludeId: number, limit = 6): SuggestedWord[] {
  if (!data) return [];
  const phonetic = data.phonetic_similar ?? [];
  const ordered: SuggestedWord[] = [
    ...phonetic.filter(w => w.phonetic_distance === 0),
    ...(data.similar_words ?? []),
    ...phonetic.filter(w => w.phonetic_distance !== 0),
  ];
  const seen = new Set<number>([excludeId]);
  return ordered.filter(w => !seen.has(w.lemma_id) && (seen.add(w.lemma_id), true)).slice(0, limit);
}
