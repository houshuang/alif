import AsyncStorage from "@react-native-async-storage/async-storage";
import { BASE_URL } from "./api";
import { enqueueReview, flushQueue } from "./sync-queue";

export interface ChapterToken { id: string; surface: string; gloss: string; note?: string }
export interface ChapterParagraph { id: string; arabic: string; english: string; tokens: ChapterToken[] }
export interface ReadingChapter {
  id: string; title: string; orientation: string; word_count: number;
  preview: { form: string; meaning: string; note: string }[];
  paragraphs: ChapterParagraph[];
}
export interface ChapterBook { id: "library-bridge"; version: 1; title: string; source: string; chapters: ReadingChapter[] }
export interface VoiceDraft { base64: string; mimeType: "audio/webm" | "audio/mp4" | "audio/ogg"; durationMs: number }
export interface ChapterProgress {
  attemptId: string; stage: "preview" | "reading" | "finished";
  vowels: boolean; translations: string[]; scrollY: number;
  effort: "smooth" | "some-work" | "tiring" | null;
  text: string; voice: VoiceDraft | null; feedbackSaved: boolean;
}
export type ChapterEventKind = "open" | "start" | "word" | "translation" | "vowels" | "preview" | "pause" | "complete" | "feedback" | "reread";
export interface ChapterEvent extends Record<string, unknown> {
  client_event_id: string; attempt_id: string; reader_id: "library-bridge"; version: 1;
  chapter_id: string; occurred_at: string; kind: ChapterEventKind;
  stage: ChapterProgress["stage"]; vowels: boolean;
}
interface Pending { type: "reading_chapter_event" | "reading_chapter_voice"; id: string; payload: Record<string, unknown> }
interface Journal { lastChapter: string; chapters: Record<string, ChapterProgress>; outbox: Pending[] }
export const CHAPTER_STORAGE_KEY = "@alif:chapters:library-bridge:v1";
const CACHE_KEY = "@alif:chapters:content:v1";
let lock: Promise<unknown> = Promise.resolve();
function locked<T>(work: () => Promise<T>): Promise<T> {
  const next = lock.then(work, work); lock = next.catch(() => {}); return next;
}
export function chapterId(): string {
  return `chapter:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 12)}`;
}
export function freshChapter(): ChapterProgress {
  return { attemptId: chapterId(), stage: "preview", vowels: true, translations: [], scrollY: 0,
    effort: null, text: "", voice: null, feedbackSaved: false };
}
export function validChapterBook(value: unknown): value is ChapterBook {
  const book = value as ChapterBook;
  return !!book && book.id === "library-bridge" && book.version === 1
    && typeof book.title === "string" && Array.isArray(book.chapters) && book.chapters.length > 0
    && book.chapters.every(c => typeof c.id === "string" && typeof c.title === "string"
      && typeof c.orientation === "string" && Number.isInteger(c.word_count)
      && Array.isArray(c.preview) && c.preview.length <= 6
      && c.preview.every(w => typeof w.form === "string" && typeof w.meaning === "string" && typeof w.note === "string")
      && Array.isArray(c.paragraphs) && c.paragraphs.length > 0
      && c.paragraphs.every(p => typeof p.id === "string" && typeof p.arabic === "string" && typeof p.english === "string"
        && Array.isArray(p.tokens) && p.tokens.length > 0
        && p.tokens.every(t => typeof t.id === "string" && typeof t.surface === "string" && typeof t.gloss === "string")));
}
export async function getChapterBook(): Promise<ChapterBook> {
  const raw = await AsyncStorage.getItem(CACHE_KEY).catch(() => null);
  if (raw) { try { const cached = JSON.parse(raw); if (validChapterBook(cached)) return cached; } catch { /* refetch */ } }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(`${BASE_URL}/api/books/chapters`, { signal: controller.signal });
    if (!response.ok) throw new Error("Couldn’t download the chapters. Please reconnect and retry.");
    const data = await response.json();
    if (!validChapterBook(data)) throw new Error("Unsupported chapter content");
    await AsyncStorage.setItem(CACHE_KEY, JSON.stringify(data));
    return data;
  } finally { clearTimeout(timer); }
}
function validProgress(p: ChapterProgress): boolean {
  return !!p && typeof p.attemptId === "string" && !!p.attemptId
    && ["preview", "reading", "finished"].includes(p.stage) && typeof p.vowels === "boolean"
    && Array.isArray(p.translations) && p.translations.every(t => typeof t === "string")
    && Number.isFinite(p.scrollY) && p.scrollY >= 0 && typeof p.text === "string" && p.text.length <= 4000
    && [null, "smooth", "some-work", "tiring"].includes(p.effort) && typeof p.feedbackSaved === "boolean"
    && (p.voice === null || (!!p.voice && typeof p.voice.base64 === "string" && p.voice.base64.length <= 2_000_000
      && ["audio/webm", "audio/mp4", "audio/ogg"].includes(p.voice.mimeType)
      && Number.isInteger(p.voice.durationMs) && p.voice.durationMs > 0 && p.voice.durationMs <= 65000));
}
async function readJournal(): Promise<Journal> {
  const raw = await AsyncStorage.getItem(CHAPTER_STORAGE_KEY);
  if (!raw) return { lastChapter: "drawing", chapters: {}, outbox: [] };
  const j = JSON.parse(raw) as Journal;
  if (!j || typeof j.lastChapter !== "string" || !j.chapters || typeof j.chapters !== "object"
    || Array.isArray(j.chapters) || !Object.values(j.chapters).every(validProgress)
    || !Array.isArray(j.outbox)) throw new Error("Couldn’t read saved progress. It has been kept on this device.");
  return j;
}
async function drain(journal: Journal): Promise<void> {
  for (const item of journal.outbox) await enqueueReview(item.type, item.payload, item.id);
  if (journal.outbox.length) await AsyncStorage.setItem(CHAPTER_STORAGE_KEY, JSON.stringify({ ...journal, outbox: [] }));
  void flushQueue().catch(() => {});
}
export function loadChapterProgress(): Promise<Journal> {
  return locked(async () => { const j = await readJournal(); await drain(j).catch(() => {}); return j; });
}
export function updateChapterProgress(id: string, change: (p: ChapterProgress) => ChapterProgress,
  kind?: ChapterEventKind, extra: Record<string, unknown> = {}, sendVoice = false): Promise<ChapterProgress> {
  return locked(async () => {
    const journal = await readJournal();
    const previous = journal.chapters[id] ?? freshChapter();
    if ((kind === "complete" && previous.stage === "finished") || (kind === "feedback" && previous.feedbackSaved)) return previous;
    const next = change(previous);
    if (!validProgress(next)) throw new Error("Invalid reading progress");
    if (kind) {
      const event: ChapterEvent = { client_event_id: chapterId(), attempt_id: next.attemptId,
        reader_id: "library-bridge", version: 1, chapter_id: id,
        occurred_at: new Date().toISOString(), kind, stage: next.stage, vowels: next.vowels, ...extra };
      if (kind === "feedback") { event.text = next.text || null; event.effort = next.effort; }
      const voice = sendVoice ? next.voice : null;
      journal.outbox.push({ id: event.client_event_id,
        type: voice ? "reading_chapter_voice" : "reading_chapter_event",
        payload: voice ? { event, audio_base64: voice.base64, mime_type: voice.mimeType, duration_ms: voice.durationMs } : event });
      if (kind === "feedback") { next.voice = null; next.feedbackSaved = true; }
    }
    const lastChapter = kind === "open" || kind === "start" || kind === "reread" ? id : journal.lastChapter;
    const updated = { ...journal, lastChapter, chapters: { ...journal.chapters, [id]: next } };
    // Progress, draft and unsent evidence commit atomically before the UI advances.
    await AsyncStorage.setItem(CHAPTER_STORAGE_KEY, JSON.stringify(updated));
    if (kind) await drain(updated).catch(() => {});
    return next;
  });
}

export function withoutVowels(text: string): string { return text.replace(/[\u064B-\u0652\u0670]/g, ""); }
