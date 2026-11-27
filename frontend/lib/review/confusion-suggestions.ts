import { ConfusionAnalysis, ConfusionCaptureIn } from "../types";

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

/** Same list as the backend's FORGET_TEXTS: such notes are forgetting, not a mix-up. */
const FORGET_TEXTS = new Set(["forget", "forgot", "forgotten", "don't know", "dont know", "no idea", "?"]);

/** A named mix-up retires the word's missed-word cards; pair practice takes over. */
export function isNamedMixUp(capture: ConfusionCaptureIn | null | undefined): boolean {
  if (!capture) return false;
  if (capture.capture_method === "suggested_pick") return capture.confused_with_lemma_id != null;
  const text = (capture.confused_with_text ?? "").trim().toLowerCase();
  return text.length > 0 && !FORGET_TEXTS.has(text);
}
