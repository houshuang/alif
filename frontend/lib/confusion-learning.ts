import AsyncStorage from "@react-native-async-storage/async-storage";

export interface PairWord { id: number; arabic: string; gloss: string; transliteration: string; root: string | null }
export interface PairQuestion { options?: {lemma_id: number; arabic: string}[]; kind: "meaning" | "cloze"; prompt: string; translation?: string; answer_lemma_id: number; feedback: string }
export interface PairLesson { version: number; distinction: string; a_cue: string; b_cue: string; questions: PairQuestion[] }
export interface ConfusionPair {
  offline?: boolean; id: number; a: PairWord; b: PairWord; lesson: PairLesson | null;
  generation_state: string; generation_error: string | null;
  status: "active" | "partially_resolved" | "resolved";
  /** Server spacing: due_now pairs may appear in reading; practice_start is the run's first question. */
  due_now: boolean; practice_step: number; practice_start: number; practice_retired: boolean; next_due: string | null;
  practice_correct: number; practice_attempts: number; practice_total_correct: number; clean_contexts: number; clean_days: number; reports: number; last_session_id: string | null; last_report: string | null; explanation: string;
}
export interface UnresolvedConfusion { capture_id: number; word: PairWord; text: string; session_id: string | null; state: string; candidates: PairWord[] }
export interface ConfusionLibrary { pairs: ConfusionPair[]; unresolved: UnresolvedConfusion[]; offline?: boolean }
export const CONFUSION_LIBRARY_KEY = "@alif/confusion-library";
export const pairStatusLabel = (status: ConfusionPair["status"]) => ({ active: "Still confused", partially_resolved: "Improving", resolved: "Resolved" }[status]);

// Last reading run (finished or closed) per pair; keeps a pair away offline or before the server catches up.
const PRACTICE_KEY = "@alif/confusion-practice-done";
export const PRACTICE_REST_MS = 20 * 3600 * 1000;
export async function loadPracticeDone(): Promise<Record<string, number>> {
  try { return JSON.parse(await AsyncStorage.getItem(PRACTICE_KEY) || "{}"); } catch { return {}; }
}
export async function savePracticeDone(pairId: number, at = Date.now()) {
  const done = await loadPracticeDone();
  done[pairId] = at;
  await AsyncStorage.setItem(PRACTICE_KEY, JSON.stringify(done));
}
// The server sends naive UTC timestamps.
const reportedAt = (p: ConfusionPair) => p.last_report ? Date.parse(p.last_report.endsWith("Z") ? p.last_report : p.last_report + "Z") : 0;
/** Pairs reading may show now: lesson ready, due on the server, not resolved, not run here recently. */
export function readyPairs(library: ConfusionLibrary | null, done: Record<string, number>, shown: Set<number>, at = Date.now()) {
  return (library?.pairs ?? []).filter(p => p.lesson && p.due_now && p.status !== "resolved" && !shown.has(p.id)
    && !(at - (done[p.id] ?? 0) < PRACTICE_REST_MS && (done[p.id] ?? 0) > reportedAt(p)));
}
