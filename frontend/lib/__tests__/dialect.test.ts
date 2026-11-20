import {
  expressionQueue,
  expressionsAt,
  linesByExpression,
  replaySpeed,
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

describe("replaySpeed", () => {
  it("steps down from the start speed and stops at the slowest", () => {
    expect([0, 1, 2, 3].map((n) => replaySpeed(1, n))).toEqual([1, 0.75, 0.6, 0.6]);
    expect([0, 1].map((n) => replaySpeed(0.75, n))).toEqual([0.75, 0.6]);
  });
  it("treats an unknown start speed as the fastest", () => {
    expect(replaySpeed(0.9, 0)).toBe(1);
  });
});

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

describe("sourceLines", () => {
  it("keeps catalogue order for the chosen sources", () => {
    expect(sourceLines(catalogue, ["b", "a"])).toEqual(["a1", "a2", "a3", "b1"]);
  });
});
