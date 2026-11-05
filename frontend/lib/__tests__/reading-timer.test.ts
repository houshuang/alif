import { ReadingTimer } from "../review/reading-timer";

describe("reading fluency clock", () => {
  let mono: number, wall: number, timer: ReadingTimer;
  const tick = (ms: number) => { mono += ms; wall += ms; timer.heartbeat(); };
  beforeEach(() => { mono = 10; wall = 100000; timer = new ReadingTimer(() => mono, () => wall); });
  it("times only the front and freezes at first reveal, excluding answer-side deliberation", () => {
    timer.start(); tick(1400); expect(timer.finish()).toEqual({ version: 1, elapsed_ms: 1400, exclusions: [] });
    tick(90000); expect(timer.finish().elapsed_ms).toBe(1400);
  });
  it("invalidates a distracted presentation instead of timing a practiced reread", () => {
    timer.start(); tick(1000); timer.interrupt("background"); tick(1000); timer.start();
    expect(timer.finish()).toEqual({ version: 1, elapsed_ms: 2000, exclusions: ["background"] });
  });
  it("does not count manual reset as a fresh fluent reading", () => {
    timer.start(); tick(800); timer.exclude("manual"); expect(timer.finish().exclusions).toContain("manual");
  });
  it("retains assistance and marks even when the learner later clears them", () => {
    timer.start(); tick(1000); timer.exclude("assistance"); timer.exclude("marked_word");
    expect(timer.finish().exclusions).toEqual(["assistance", "marked_word"]);
  });
  it("detects a suspended JS thread even without an app-state notification", () => {
    timer.start(); mono += 30000; wall += 30000;
    expect(timer.finish().exclusions).toContain("clock_gap");
  });
  it("detects a sleep clock discrepancy and a backward clock", () => {
    timer.start(); mono += 100; wall += 30000; timer.heartbeat();
    expect(timer.finish().exclusions).toContain("clock_gap");
    timer = new ReadingTimer(() => mono, () => wall); timer.start();
    mono -= 500; expect(timer.finish().exclusions).toContain("clock_error");
  });
  it("does not count an answer before the visible-card frame", () => {
    expect(timer.finish()).toEqual({ version: 1, elapsed_ms: null, exclusions: ["not_started"] });
  });
  it("retains after-reveal help but ignores interruptions after the measurement ended", () => {
    timer.start(); tick(1400); timer.finish(); timer.interrupt("background");
    expect(timer.finish().exclusions).toEqual([]);
    timer.exclude("assistance"); expect(timer.finish().exclusions).toEqual(["assistance"]);
  });
});
