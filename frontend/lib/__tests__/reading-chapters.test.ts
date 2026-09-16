import AsyncStorage from "@react-native-async-storage/async-storage";
import { CHAPTER_STORAGE_KEY, freshChapter, getChapterBook, loadChapterProgress, updateChapterProgress,
  validChapterBook, withoutVowels } from "../reading-chapters";
import { enqueueReview, flushQueue } from "../sync-queue";
const content = require("../../../backend/app/data/reading_chapters_v1.json");
jest.mock("../api", () => ({ BASE_URL: "http://local.test" }));
jest.mock("../sync-queue", () => ({ enqueueReview: jest.fn().mockResolvedValue(undefined), flushQueue: jest.fn().mockResolvedValue({}) }));

beforeEach(async () => {
  for (const key of Object.keys((AsyncStorage as any)._store)) delete (AsyncStorage as any)._store[key];
  jest.clearAllMocks();
  (enqueueReview as jest.Mock).mockResolvedValue(undefined);
});

it("starts with a transient preview, vowel support and no English", () => {
  expect(freshChapter()).toMatchObject({ stage: "preview", vowels: true, translations: [], feedbackSaved: false, voice: null });
  expect(withoutVowels("وَرَقَةً وَرَسَمَ أَخَذَ")).toBe("ورقة ورسم أخذ");
});

it("caches the whole immutable book and validates its runtime shape", async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => content });
  expect(validChapterBook(content)).toBe(true);
  expect(validChapterBook({ ...content, chapters: [{ id: "bank" }] })).toBe(false);
  expect(await getChapterBook()).toEqual(content);
  (global.fetch as jest.Mock).mockRejectedValue(new Error("offline"));
  expect(await getChapterBook()).toEqual(content);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("saves independent places, supports offline draft recovery and never queues drafts", async () => {
  const voice = { base64: "AA==", mimeType: "audio/mp4" as const, durationMs: 1000 };
  await updateChapterProgress("drawing", p => ({ ...p, stage: "reading", scrollY: 630, text: "A slow word", voice }));
  await updateChapterProgress("bank", p => ({ ...p, stage: "reading", scrollY: 180 }));
  const j = await loadChapterProgress();
  expect(j.lastChapter).toBe("bank");
  expect(j.chapters.drawing).toMatchObject({ scrollY: 630, text: "A slow word", voice });
  expect(j.chapters.bank.scrollY).toBe(180);
  expect(enqueueReview).not.toHaveBeenCalled();
});

it("atomically retains feedback through queue failure and replays the same event ID", async () => {
  (enqueueReview as jest.Mock).mockRejectedValueOnce(new Error("storage temporarily unavailable"));
  const p = await updateChapterProgress("bank", p => ({ ...p, text: "Smooth", effort: "smooth" }), "feedback");
  expect(p.feedbackSaved).toBe(true);
  const raw = JSON.parse((await AsyncStorage.getItem(CHAPTER_STORAGE_KEY))!);
  expect(raw.outbox).toHaveLength(1);
  const id = raw.outbox[0].id;
  await loadChapterProgress();
  expect((enqueueReview as jest.Mock).mock.calls[1]).toEqual(["reading_chapter_event", expect.objectContaining({ client_event_id: id, chapter_id: "bank", text: "Smooth" }), id]);
  expect(JSON.parse((await AsyncStorage.getItem(CHAPTER_STORAGE_KEY))!).outbox).toEqual([]);
  expect(flushQueue).toHaveBeenCalled();
});

it("keeps completion separate from optional feedback and guards duplicate taps", async () => {
  await updateChapterProgress("drawing", p => ({ ...p, stage: "finished" }), "complete");
  await updateChapterProgress("drawing", p => ({ ...p, stage: "finished" }), "complete");
  expect(enqueueReview).toHaveBeenCalledTimes(1);
  expect((enqueueReview as jest.Mock).mock.calls[0][1]).toMatchObject({ kind: "complete", chapter_id: "drawing" });
  await updateChapterProgress("bank", p => ({ ...p, stage: "reading" }), "start");
  expect((await loadChapterProgress()).chapters.drawing.stage).toBe("finished");
});

it("moves a voice draft into the durable outbox before clearing it", async () => {
  const voice = { base64: "AA==", mimeType: "audio/mp4" as const, durationMs: 1500 };
  await updateChapterProgress("drawing", p => ({ ...p, voice, text: "Needed the vowels" }));
  (enqueueReview as jest.Mock).mockRejectedValueOnce(new Error("offline storage"));
  await updateChapterProgress("drawing", p => p, "feedback", {}, true);
  const j = JSON.parse((await AsyncStorage.getItem(CHAPTER_STORAGE_KEY))!);
  expect(j.chapters.drawing.voice).toBeNull();
  expect(j.outbox[0]).toMatchObject({ type: "reading_chapter_voice", payload: { audio_base64: "AA==", mime_type: "audio/mp4", duration_ms: 1500, event: { text: "Needed the vowels", kind: "feedback" } } });
});

it("serializes scroll and reflection edits without overwriting one another", async () => {
  await Promise.all([
    updateChapterProgress("bank", p => ({ ...p, scrollY: 300 })),
    updateChapterProgress("bank", p => ({ ...p, text: "The last sentence" })),
    updateChapterProgress("bank", p => ({ ...p, translations: ["bank-2"] })),
  ]);
  expect((await loadChapterProgress()).chapters.bank).toMatchObject({ scrollY: 300, text: "The last sentence", translations: ["bank-2"] });
});

it("does not overwrite corrupt progress or lose a draft on a failed local commit", async () => {
  await AsyncStorage.setItem(CHAPTER_STORAGE_KEY, "corrupt evidence");
  await expect(updateChapterProgress("bank", p => p, "open")).rejects.toThrow();
  expect(await AsyncStorage.getItem(CHAPTER_STORAGE_KEY)).toBe("corrupt evidence");
  await AsyncStorage.removeItem(CHAPTER_STORAGE_KEY);
  await updateChapterProgress("bank", p => ({ ...p, text: "Keep this" }));
  const set = jest.spyOn(AsyncStorage, "setItem").mockRejectedValueOnce(new Error("disk full"));
  await expect(updateChapterProgress("bank", p => p, "feedback")).rejects.toThrow("disk full");
  set.mockRestore();
  expect((await loadChapterProgress()).chapters.bank).toMatchObject({ text: "Keep this", feedbackSaved: false });
  expect(enqueueReview).not.toHaveBeenCalled();
});
