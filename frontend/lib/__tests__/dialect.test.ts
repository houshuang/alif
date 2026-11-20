import {
  expressionQueue,
  expressionsAt,
  lineSegments,
  linesByExpression,
  segmentStep,
  sourceLines,
  tokenAt,
} from "../dialect";
import { DialectCatalogue, DialectLine } from "../types";

function line(id: string, src: string, scene: number, ex: [string, number, number][] = []): DialectLine {
  return {
    id,
    src,
    scene,
    st: "",
    who: "",
    ar: "",
    en: "",
    s: 0,
    e: 2,
    toks: [
      { t: "شو", s: 0.1, e: 0.4 },
      { t: "بدّك", s: 0.5, e: 0.9 },
      { t: "هلأ", s: null, e: null },
    ],
    ex,
    hard: false,
  };
}

const catalogue: DialectCatalogue = {
  sources: [],
  expressions: [],
  lines: [
    line("a1", "a", 1, [["shu", 0, 0], ["badd", 1, 1]]),
    line("a2", "a", 1, [["shu", 0, 0]]),
    line("a3", "a", 2, [["shu", 0, 0], ["shu-badd", 0, 1]]),
    line("b1", "b", 1, [["shu", 0, 0], ["shu", 0, 0]]),
  ],
};

describe("expression grouping", () => {
  const byExpr = linesByExpression(catalogue);

  it("lists each line once per expression", () => {
    expect(byExpr.get("shu")).toEqual(["a1", "a2", "a3", "b1"]);
    expect(byExpr.get("badd")).toEqual(["a1"]);
  });

  it("puts other sources first, then other scenes, then the same scene, and skips the current line", () => {
    expect(expressionQueue(catalogue, byExpr, "shu", catalogue.lines[0])).toEqual(["b1", "a3", "a2"]);
  });

  it("returns nothing for an expression without other lines", () => {
    expect(expressionQueue(catalogue, byExpr, "badd", catalogue.lines[0])).toEqual([]);
    expect(expressionQueue(catalogue, byExpr, "missing", catalogue.lines[0])).toEqual([]);
  });

  it("orders overlapping expressions on a token longest first", () => {
    expect(expressionsAt(catalogue.lines[2], 0)).toEqual(["shu-badd", "shu"]);
    expect(expressionsAt(catalogue.lines[2], 2)).toEqual([]);
  });
});

describe("tokenAt", () => {
  const l = catalogue.lines[0];
  it("follows the spoken token and goes blank in long gaps", () => {
    expect(tokenAt(l, 0.05)).toBe(-1);
    expect(tokenAt(l, 0.2)).toBe(0);
    expect(tokenAt(l, 0.6)).toBe(1);
    expect(tokenAt(l, 1.5)).toBe(-1);
  });
});

describe("segments", () => {
  const segs: [number, number][] = [[17, 20.5], [41, 43]];

  it("falls back to the line's start and end", () => {
    expect(lineSegments(catalogue.lines[0])).toEqual([[0, 2]]);
    expect(lineSegments({ ...catalogue.lines[0], segs })).toEqual(segs);
  });

  it("plays inside a segment, jumps over the pause, then ends", () => {
    expect(segmentStep(segs, 0, 18000)).toEqual({ kind: "play" });
    expect(segmentStep(segs, 0, 20500)).toEqual({ kind: "next", index: 1 });
    expect(segmentStep(segs, 1, 42000)).toEqual({ kind: "play" });
    expect(segmentStep(segs, 1, 43050)).toEqual({ kind: "end" });
  });

  it("ignores positions reported from before the seek", () => {
    expect(segmentStep(segs, 1, 20600)).toEqual({ kind: "stale" });
    expect(segmentStep(segs, 0, 90000)).toEqual({ kind: "stale" });
    expect(segmentStep(segs, 0, 16900)).toEqual({ kind: "play" });
  });
});

describe("sourceLines", () => {
  it("keeps catalogue order for the chosen sources", () => {
    expect(sourceLines(catalogue, ["b", "a"])).toEqual(["a1", "a2", "a3", "b1"]);
  });
});
