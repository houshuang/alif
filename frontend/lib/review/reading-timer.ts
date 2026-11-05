/** Reading time ends at first reveal/answer. Invalidations are permanent for this
 * presentation: restarting after a distraction would time a rehearsed reread. */
export type TimingExclusion = "manual" | "background" | "navigation" | "clock_gap"
  | "clock_error" | "assistance" | "marked_word" | "not_started";
export interface ReadingTiming {
  version: 1;
  elapsed_ms: number | null;
  exclusions: TimingExclusion[];
}
export class ReadingTimer {
  private startMs: number | null = null;
  private endMs: number | null = null;
  private lastMono = 0;
  private lastWall = 0;
  private reasons = new Set<TimingExclusion>();
  constructor(private mono: () => number = () => performance.now(),
    private wall: () => number = () => Date.now()) {}
  start() {
    if (this.startMs != null) return;
    this.startMs = this.mono(); this.lastMono = this.startMs; this.lastWall = this.wall();
    this.endMs = null;
    if (!Number.isFinite(this.startMs)) this.exclude("clock_error");
  }
  exclude(reason: TimingExclusion) { this.reasons.add(reason); }
  interrupt(reason: TimingExclusion) {
    if (this.startMs != null && this.endMs == null) this.exclude(reason);
  }
  heartbeat() {
    if (this.startMs == null || this.endMs != null) return;
    const m = this.mono(), w = this.wall();
    const md = m - this.lastMono, wd = w - this.lastWall;
    if (!Number.isFinite(m) || md < 0 || wd < 0) this.exclude("clock_error");
    if (md > 5000 || wd > 5000 || Math.abs(md - wd) > 1000) this.exclude("clock_gap");
    this.lastMono = m; this.lastWall = w;
  }
  finish(): ReadingTiming {
    if (this.endMs == null && this.startMs != null) {
      this.heartbeat(); this.endMs = this.mono();
    }
    if (this.startMs == null) this.exclude("not_started");
    const elapsed = this.startMs != null && this.endMs != null ? this.endMs - this.startMs : null;
    if (elapsed != null && (!Number.isFinite(elapsed) || elapsed <= 0)) this.exclude("clock_error");
    return { version: 1, elapsed_ms: elapsed != null && Number.isFinite(elapsed) && elapsed > 0
      ? Math.round(elapsed) : null, exclusions: [...this.reasons] };
  }
}
