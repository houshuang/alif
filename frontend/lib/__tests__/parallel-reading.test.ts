import AsyncStorage from "@react-native-async-storage/async-storage";
import { arabicFor, changeParallelProgress, currentParallelProgress, freshParallelJournal,
  loadParallelJournal, parallelLibrary, PARALLEL_KEY, updateParallelJournal } from "../parallel-reading";
import { enqueueReview } from "../sync-queue";
jest.mock("../sync-queue", () => ({ enqueueReview: jest.fn().mockResolvedValue(undefined), flushQueue: jest.fn().mockResolvedValue({}) }));
beforeEach(async () => { await AsyncStorage.clear(); jest.clearAllMocks(); (enqueueReview as jest.Mock).mockResolvedValue(undefined); });
it("bundles the same immutable content validated by the server, with provenance and all four versions", () => {
  const canonical = require("../../../backend/app/data/parallel_reading_v1.json");
  expect(parallelLibrary).toEqual(canonical);
  expect(new Set(parallelLibrary.texts.map(t => t.id)).size).toBe(parallelLibrary.texts.length);
  for (const t of parallelLibrary.texts) {
    expect(t.source_url).toMatch(/^https:/); expect(t.source_note).toBeTruthy();
    expect(new Set(t.paragraphs.map(p => p.id)).size).toBe(t.paragraphs.length);
    for (const p of t.paragraphs) for (const lang of ["ar", "grc", "la", "ru"] as const) expect(p[lang]).toBeTruthy();
  }
});
it("keeps an independent bookmark for each reading, including Arabic reread, across reopening", async () => {
  await updateParallelJournal(j => changeParallelProgress({ ...j, library: false, revealed: true, support: "ru" }, { paragraph: 2, reread: true, completed: true }), "complete");
  await updateParallelJournal(j => changeParallelProgress({ ...j, textId: "kalila-net" }, { paragraph: 3 }), "select");
  const saved = await loadParallelJournal();
  expect(saved).toMatchObject({ library: false, textId: "kalila-net", support: "ru", revealed: true });
  expect(saved.progress["aesop-jar"]).toEqual({ paragraph: 2, reread: true, completed: true });
  expect(currentParallelProgress(saved).paragraph).toBe(3);
  expect((enqueueReview as jest.Mock).mock.calls.every(c => c[0] === "parallel_reading_event")).toBe(true);
});
it("atomically retains events through failed handoff and retries with the same identity", async () => {
  (enqueueReview as jest.Mock).mockRejectedValueOnce(new Error("offline storage"));
  await updateParallelJournal(j => ({ ...j, library: false }), "select");
  const raw = JSON.parse((await AsyncStorage.getItem(PARALLEL_KEY))!);
  expect(raw.library).toBe(false); expect(raw.outbox).toHaveLength(1);
  await loadParallelJournal();
  expect((enqueueReview as jest.Mock).mock.calls[1][2]).toBe(raw.outbox[0].client_event_id);
  expect(JSON.parse((await AsyncStorage.getItem(PARALLEL_KEY))!).outbox).toEqual([]);
});
it("preserves corrupt bookmarks and does not advance after a failed local commit", async () => {
  await AsyncStorage.setItem(PARALLEL_KEY, "broken saved reading");
  await expect(loadParallelJournal()).rejects.toThrow();
  await expect(updateParallelJournal(j => j, "open")).rejects.toThrow();
  expect(await AsyncStorage.getItem(PARALLEL_KEY)).toBe("broken saved reading");
  await AsyncStorage.removeItem(PARALLEL_KEY);
  (AsyncStorage.setItem as jest.Mock).mockRejectedValueOnce(new Error("disk full"));
  await expect(updateParallelJournal(j => ({ ...j, library: false }), "select")).rejects.toThrow("disk full");
  expect((await loadParallelJournal()).library).toBe(true); expect(enqueueReview).not.toHaveBeenCalled();
});
it("serializes concurrent preference edits and rejects out-of-bounds places", async () => {
  await Promise.all([updateParallelJournal(j => ({ ...j, support: "la" }), "support"), updateParallelJournal(j => ({ ...j, size: 1.2 }), "size")]);
  expect(await loadParallelJournal()).toMatchObject({ support: "la", size: 1.2 });
  const before = await AsyncStorage.getItem(PARALLEL_KEY);
  await expect(updateParallelJournal(j => changeParallelProgress(j, { paragraph: 999 }), "passage")).rejects.toThrow();
  expect(await AsyncStorage.getItem(PARALLEL_KEY)).toBe(before);
});
it("only removes reading marks, preserving Arabic letters and honest partial-mark support", () => {
  const p = parallelLibrary.texts[0].paragraphs[0];
  expect(arabicFor(p, true)).toContain("جَمَعَ");
  expect(arabicFor(p, false)).toContain("جمع");
  const partial = parallelLibrary.texts[1].paragraphs[0];
  expect(arabicFor(partial, true)).toBe(partial.ar);
  expect(freshParallelJournal().revealed).toBe(false);
});
