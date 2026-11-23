import AsyncStorage from "@react-native-async-storage/async-storage";

export interface PairWord { id: number; arabic: string; gloss: string; transliteration: string; root: string | null }
export interface PairQuestion { options?: {lemma_id: number; arabic: string}[]; kind: "meaning" | "cloze"; prompt: string; translation?: string; answer_lemma_id: number; feedback: string }
export interface PairLesson { version: number; distinction: string; a_cue: string; b_cue: string; questions: PairQuestion[] }
export interface ConfusionPair {
  offline?: boolean; id: number; a: PairWord; b: PairWord; lesson: PairLesson | null;
  generation_state: string; generation_error: string | null;
  status: "active" | "partially_resolved" | "resolved";
  practice_correct: number; practice_attempts: number; practice_total_correct: number; clean_contexts: number; clean_days: number; reports: number; last_session_id: string | null; last_report: string | null; explanation: string;
}
export interface UnresolvedConfusion { capture_id: number; word: PairWord; text: string; session_id: string | null; state: string; candidates: PairWord[] }
export interface ConfusionLibrary { pairs: ConfusionPair[]; unresolved: UnresolvedConfusion[]; offline?: boolean }
export const CONFUSION_LIBRARY_KEY = "@alif/confusion-library";
export const pairStatusLabel = (status: ConfusionPair["status"]) => ({ active: "Still confused", partially_resolved: "Improving", resolved: "Resolved" }[status]);

const PRACTICE_KEY = "@alif/confusion-practice-turns";
export async function loadPracticeTurns(): Promise<Record<string, { next: number; lastReport: string | null }>> {
  try { return JSON.parse(await AsyncStorage.getItem(PRACTICE_KEY) || "{}"); } catch { return {}; }
}
export async function savePracticeTurn(pair: ConfusionPair, next: number) {
  const turns = await loadPracticeTurns();
  turns[pair.id] = { next, lastReport: pair.last_report };
  await AsyncStorage.setItem(PRACTICE_KEY, JSON.stringify(turns));
}
