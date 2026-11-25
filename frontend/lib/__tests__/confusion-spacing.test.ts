import { ConfusionPair, PRACTICE_REST_MS, readyPairs } from "../confusion-learning";

jest.mock("@react-native-async-storage/async-storage", () => ({ getItem: jest.fn(), setItem: jest.fn() }));

const pair = (id: number, extra: Partial<ConfusionPair> = {}) => ({
  id, lesson: {questions: []}, due_now: true, status: "active", last_report: "2026-11-24T08:00:00", ...extra,
}) as unknown as ConfusionPair;
const at = Date.parse("2026-11-25T12:00:00Z");

describe("reading shows only due, rested confusion pairs", () => {
  it("skips pairs the server has not made due, resolved pairs and pairs without a lesson", () => {
    const library = {pairs: [pair(1), pair(2, {due_now: false}), pair(3, {status: "resolved"}), pair(4, {lesson: null})], unresolved: []};
    expect(readyPairs(library, {}, new Set(), at).map(p => p.id)).toEqual([1]);
  });

  it("shows a pair at most once per session", () => {
    expect(readyPairs({pairs: [pair(1)], unresolved: []}, {}, new Set([1]), at)).toEqual([]);
  });

  it("rests a finished or closed pair locally, unless it was reported again since", () => {
    const done = {1: at - 3600 * 1000, 2: at - 3600 * 1000, 3: at - PRACTICE_REST_MS - 1};
    const library = {pairs: [pair(1), pair(2, {last_report: "2026-11-25T11:30:00"}), pair(3)], unresolved: []};
    expect(readyPairs(library, done, new Set(), at).map(p => p.id)).toEqual([2, 3]);
  });
});
