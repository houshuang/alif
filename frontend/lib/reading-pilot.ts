import AsyncStorage from "@react-native-async-storage/async-storage";
import { BASE_URL } from "./api";
import { enqueueReview, flushQueue } from "./sync-queue";

export interface PilotHelp { id: string; anchor: string; form: string; meaning: string; explanation: string }
export interface PilotBlock { id: string; arabic: string; english: string; help: PilotHelp[] }
export interface PilotSession {
  id: string; title: string; orientation: string; source_pages: number[];
  blocks: PilotBlock[]; simpler_arabic: string; simpler_english: string;
}
export interface ReadingPilot {
  id: "momo-wings"; version: 1; title: string; author: string;
  source: { chapter: number; printed_pages: number[]; note: string };
  sessions: PilotSession[];
}
export type PilotPhase = "read" | "reread";
export interface PilotProgress {
  index: number; attemptId: string; sequence: number; phase: PilotPhase;
  english: boolean; simpler: boolean; phraseHelp: boolean; openHelp: string[];
  readingMs: number; rereadingMs: number;
  followed: "yes" | "partly" | "no" | null;
  wantedMore: "yes" | "maybe" | "no" | null;
  finished: boolean;
}
export type PilotEventKind = "open" | "translation" | "phrase_help" | "help" | "simpler" | "reread" | "feedback" | "complete" | "pause";
export interface PilotEvent extends Record<string, unknown> {
  client_event_id: string; attempt_id: string; pilot_id: "momo-wings"; version: 1;
  session_id: string; occurred_at: string; kind: PilotEventKind; phase: PilotPhase;
  reading_ms: number; rereading_ms: number;
}
const CACHE_KEY = "@alif:reading-pilot:content:v1";
export const PILOT_STORAGE_KEY = "@alif:reading-pilot:momo-wings:v1";
interface Journal { progress: PilotProgress; outbox: PilotEvent[] }
let journalLock: Promise<unknown> = Promise.resolve();
function locked<T>(work: () => Promise<T>): Promise<T> {
  const result = journalLock.then(work, work);
  journalLock = result.catch(() => {});
  return result;
}

export function freshPilotProgress(index = 0): PilotProgress {
  return {
    index, attemptId: `rp:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 12)}`,
    sequence: 0, phase: "read", english: true, simpler: false, phraseHelp: false, openHelp: [],
    readingMs: 0, rereadingMs: 0, followed: null, wantedMore: null, finished: false,
  };
}

export function parsePilotProgress(value: unknown, count: number): PilotProgress | null {
  if (!value || typeof value !== "object") return null;
  const p = value as PilotProgress;
  if (!Number.isInteger(p.index) || p.index < 0 || p.index >= count
    || typeof p.attemptId !== "string" || !p.attemptId
    || !Number.isInteger(p.sequence) || p.sequence < 0
    || !["read", "reread"].includes(p.phase)
    || ![p.english, p.simpler, p.phraseHelp, p.finished].every(v => typeof v === "boolean")
    || !Array.isArray(p.openHelp) || !p.openHelp.every(v => typeof v === "string")
    || ![p.readingMs, p.rereadingMs].every(v => Number.isInteger(v) && v >= 0 && v <= 86_400_000)
    || ![null, "yes", "partly", "no"].includes(p.followed)
    || ![null, "yes", "maybe", "no"].includes(p.wantedMore)) return null;
  return p;
}

export function rereadProgress(p: PilotProgress): PilotProgress {
  return { ...p, phase: "reread", english: false, simpler: false, phraseHelp: false, openHelp: [] };
}

export function nextPilotProgress(p: PilotProgress, count: number): PilotProgress {
  return p.index + 1 < count ? freshPilotProgress(p.index + 1) : { ...p, finished: true };
}

export function pilotEvent(p: PilotProgress, sessionId: string, kind: PilotEventKind,
  extra: Record<string, unknown> = {}): PilotEvent {
  return {
    client_event_id: `${p.attemptId}:${p.sequence}`, attempt_id: p.attemptId,
    pilot_id: "momo-wings", version: 1, session_id: sessionId,
    occurred_at: new Date().toISOString(), kind, phase: p.phase,
    reading_ms: p.readingMs, rereading_ms: p.rereadingMs,
    english_visible: p.english, simpler_visible: p.simpler,
    phrase_help_visible: p.phase === "read" || p.phraseHelp,
    open_help_ids: p.openHelp, ...extra,
  };
}

async function drainOutbox(journal: Journal): Promise<void> {
  // A crash after enqueue but before clearing may resend an ID. The server's
  // primary key makes this harmless; never sacrifice an interaction on failure.
  for (const event of journal.outbox) {
    await enqueueReview("reading_pilot_event", event, event.client_event_id);
  }
  await AsyncStorage.setItem(PILOT_STORAGE_KEY, JSON.stringify({ ...journal, outbox: [] }));
  void flushQueue().catch(() => {});
}

export function savePilotProgress(progress: PilotProgress, event: PilotEvent): Promise<void> {
  return locked(async () => {
    const raw = await AsyncStorage.getItem(PILOT_STORAGE_KEY);
    const prior: Journal | null = raw ? JSON.parse(raw) : null;
    const journal: Journal = { progress, outbox: [...(prior?.outbox ?? []), event] };
    // One atomic local write saves both continuation and its evidence offline.
    await AsyncStorage.setItem(PILOT_STORAGE_KEY, JSON.stringify(journal));
    await drainOutbox(journal).catch(() => {});
  });
}

export function loadPilotProgress(count: number): Promise<PilotProgress> {
  return locked(async () => {
    const raw = await AsyncStorage.getItem(PILOT_STORAGE_KEY);
    if (!raw) return freshPilotProgress();
    // Fail visibly on corrupt storage instead of silently erasing reading history.
    const journal = JSON.parse(raw) as Journal;
    const progress = parsePilotProgress(journal.progress, count);
    if (!progress || !Array.isArray(journal.outbox)) throw new Error("Unreadable reading progress");
    await drainOutbox(journal).catch(() => {});
    return progress;
  });
}

function validContent(value: ReadingPilot): boolean {
  return value.id === "momo-wings" && value.version === 1
    && Array.isArray(value.sessions) && value.sessions.length === 3
    && value.sessions.every(s => s.blocks?.length > 0);
}
export async function getReadingPilot(): Promise<ReadingPilot> {
  // Prefer immutable cached material so a five-minute session never waits on a
  // connection. A future revision must use a new cache/content version.
  const raw = await AsyncStorage.getItem(CACHE_KEY).catch(() => null);
  if (raw) {
    try { const cached = JSON.parse(raw); if (validContent(cached)) return cached; } catch { /* fetch below */ }
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(`${BASE_URL}/api/books/reading-pilot`, { signal: controller.signal });
    if (!response.ok) throw new Error(`Reading unavailable (${response.status})`);
    const data: ReadingPilot = await response.json();
    if (!validContent(data)) throw new Error("Unsupported reading version");
    await AsyncStorage.setItem(CACHE_KEY, JSON.stringify(data));
    return data;
  } finally { clearTimeout(timer); }
}
