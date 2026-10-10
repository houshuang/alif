import AsyncStorage from "@react-native-async-storage/async-storage";
import data from "./data/parallel-reading-v2.json";
import { enqueueReview, flushQueue } from "./sync-queue";

export type SupportLanguage = "grc" | "la" | "ru" | "en";
export interface ParallelToken { id: string; surface: string; gloss: string | null }
export interface ParallelParagraph {
  id: string; ar: string; ar_vowelled: string | null; grc: string; la: string; ru: string; en: string; tokens: ParallelToken[];
  clues: { id: string; form: string; meaning: string; bridge: string }[];
}
export interface ParallelText {
  id: string; title: string; author: string; description: string; original_language: string;
  source_url: string; source_note: string; versions: Record<string, string>; paragraphs: ParallelParagraph[];
}
export const parallelLibrary = data as { id: string; version: number; texts: ParallelText[] };
export const PARALLEL_KEY = "@alif:parallel-reading:v2";
export const LEGACY_PARALLEL_KEY = "@alif:parallel-reading:v1";
export interface ParallelProgress { paragraph: number; reread: boolean; completed: boolean }
export interface ParallelJournal {
  textId: string; library: boolean; support: SupportLanguage; revealed: boolean; all: boolean;
  vowels: boolean; size: number; progress: Record<string, ParallelProgress>; outbox: ParallelEvent[];
}
export type ParallelAction = "open" | "leave" | "library" | "select" | "passage" | "support" | "reveal" | "all" | "vowels" | "size" | "clue" | "word" | "display" | "about" | "reread" | "complete" | "reflection";
export interface ParallelEvent extends Record<string, unknown> {
  client_event_id: string; reader_id: "parallel-reading"; version: 1 | 2; text_id: string;
  paragraph_id: string; occurred_at: string; kind: ParallelAction;
  support: SupportLanguage; revealed: boolean; all: boolean; vowels: boolean; size: number;
  reread: boolean; completed: boolean;
}
export function freshParallelJournal(): ParallelJournal {
  return { textId: parallelLibrary.texts[0].id, library: true, support: "grc", revealed: false,
    all: false, vowels: false, size: 1, progress: {}, outbox: [] };
}
export function currentParallelText(j: ParallelJournal): ParallelText {
  return parallelLibrary.texts.find(t => t.id === j.textId)!;
}
export function currentParallelProgress(j: ParallelJournal): ParallelProgress {
  return j.progress[j.textId] ?? { paragraph: 0, reread: false, completed: false };
}
function validJournal(j: ParallelJournal): boolean {
  if (!j || !parallelLibrary.texts.some(t => t.id === j.textId) || !["grc", "la", "ru", "en"].includes(j.support)
    || ![j.library, j.revealed, j.all, j.vowels].every(x => typeof x === "boolean")
    || !Number.isFinite(j.size) || j.size < .85 || j.size > 1.4 || !j.progress || Array.isArray(j.progress)
    || typeof j.progress !== "object" || !Array.isArray(j.outbox)) return false;
  return Object.entries(j.progress).every(([id, p]) => {
    const t = parallelLibrary.texts.find(t => t.id === id);
    return !!t && !!p && Number.isInteger(p.paragraph) && p.paragraph >= 0 && p.paragraph < t.paragraphs.length
      && typeof p.reread === "boolean" && typeof p.completed === "boolean";
  }) && j.outbox.every(e => !!e && typeof e.client_event_id === "string" && e.reader_id === "parallel-reading" && [1, 2].includes(e.version));
}
let lock: Promise<unknown> = Promise.resolve();
function locked<T>(fn: () => Promise<T>): Promise<T> { const next = lock.then(fn, fn); lock = next.catch(() => {}); return next; }
async function readJournal(): Promise<ParallelJournal> {
  const raw = await AsyncStorage.getItem(PARALLEL_KEY) ?? await AsyncStorage.getItem(LEGACY_PARALLEL_KEY);
  if (!raw) return freshParallelJournal();
  const j = JSON.parse(raw);
  if (!validJournal(j)) throw new Error("Your saved reading could not be opened. It has been preserved on this device.");
  return j;
}
async function drain(j: ParallelJournal): Promise<void> {
  for (const event of j.outbox) await enqueueReview("parallel_reading_event", event, event.client_event_id);
  if (j.outbox.length) await AsyncStorage.setItem(PARALLEL_KEY, JSON.stringify({ ...j, outbox: [] }));
  void flushQueue().catch(() => {});
}
export function loadParallelJournal(): Promise<ParallelJournal> {
  return locked(async () => { const j = await readJournal(); await drain(j).catch(() => {}); return j; });
}
export function updateParallelJournal(change: (j: ParallelJournal) => ParallelJournal, kind: ParallelAction,
  extra: Record<string, unknown> = {}): Promise<ParallelJournal> {
  return locked(async () => {
    const j = change(await readJournal());
    if (!validJournal(j)) throw new Error("Invalid reading bookmark");
    const progress = currentParallelProgress(j), text = currentParallelText(j);
    const event: ParallelEvent = { ...extra,
      client_event_id: `parallel:${Date.now().toString(36)}:${Math.random().toString(36).slice(2)}`,
      reader_id: "parallel-reading", version: 2, text_id: text.id,
      paragraph_id: typeof extra.paragraph_id === "string" ? extra.paragraph_id : text.paragraphs[progress.paragraph].id, occurred_at: new Date().toISOString(), kind,
      support: j.support, revealed: j.revealed, all: j.all, vowels: j.vowels, size: j.size,
      reread: progress.reread, completed: progress.completed };
    j.outbox.push(event);
    // Bookmark and unsent event commit together before navigation advances.
    await AsyncStorage.setItem(PARALLEL_KEY, JSON.stringify(j));
    await drain(j).catch(() => {});
    return j;
  });
}
export function changeParallelProgress(j: ParallelJournal, change: Partial<ParallelProgress>): ParallelJournal {
  return { ...j, progress: { ...j.progress, [j.textId]: { ...currentParallelProgress(j), ...change } } };
}
export function arabicFor(p: ParallelParagraph, vowels: boolean): string {
  return vowels ? (p.ar_vowelled ?? p.ar) : p.ar.replace(/[\u064B-\u0652\u0670]/g, "");
}


// Display preserves source whitespace/punctuation. Identities stay anchored to
// the exact source token even when vowel marks are hidden or shown.
export function parallelWordRuns(p: ParallelParagraph, vowels: boolean):
  { text: string; token: ParallelToken | null }[] {
  let index = 0;
  return arabicFor(p, vowels).split(/(\s+)/).filter(Boolean).map(text => {
    if (/^\s+$/.test(text)) return { text, token: null };
    const token = p.tokens[index++];
    if (!token || token.surface.replace(/[\u064B-\u0652\u0670]/g, "") !== text.replace(/[\u064B-\u0652\u0670]/g, "")) {
      throw new Error("Word help does not match this edition");
    }
    return { text, token: token.gloss ? token : null };
  });
}
