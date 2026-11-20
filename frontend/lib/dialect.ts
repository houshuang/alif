import { DialectCatalogue, DialectLine } from "./types";

export const DIALECT_SPEEDS = [1, 0.75, 0.6];

export function linesByExpression(catalogue: DialectCatalogue): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const line of catalogue.lines) {
    for (const id of new Set(line.ex.map(([exprId]) => exprId))) {
      const list = map.get(id);
      if (list) list.push(line.id);
      else map.set(id, [line.id]);
    }
  }
  return map;
}

/**
 * The other lines with an expression: lines from other sources first, then other
 * scenes of the same source, then the same scene, each in catalogue order.
 */
export function expressionQueue(
  catalogue: DialectCatalogue,
  byExpression: Map<string, string[]>,
  exprId: string,
  from: DialectLine,
): string[] {
  const byId = new Map(catalogue.lines.map((l) => [l.id, l]));
  const rank = (l: DialectLine) => (l.src !== from.src ? 0 : l.scene !== from.scene ? 1 : 2);
  return (byExpression.get(exprId) ?? [])
    .filter((id) => id !== from.id)
    .map((id) => byId.get(id)!)
    .map((l, i) => ({ l, i }))
    .sort((a, b) => rank(a.l) - rank(b.l) || a.i - b.i)
    .map(({ l }) => l.id);
}

/** Expressions covering a token, longest span first. */
export function expressionsAt(line: DialectLine, token: number): string[] {
  return line.ex
    .filter(([, a, b]) => token >= a && token <= b)
    .sort((x, y) => y[2] - y[1] - (x[2] - x[1]))
    .map(([id]) => id);
}

/** Index of the token being spoken at `seconds`, or -1 between and outside tokens. */
export function tokenAt(line: DialectLine, seconds: number): number {
  let found = -1;
  line.toks.forEach((t, i) => {
    if (t.s != null && t.s <= seconds) found = i;
  });
  if (found >= 0) {
    const e = line.toks[found].e;
    if (e != null && seconds > e + 0.25) return -1;
  }
  return found;
}

export function lineSegments(line: DialectLine): [number, number][] {
  return line.segs && line.segs.length > 0 ? line.segs : [[line.s, line.e]];
}

export type SegmentStep = { kind: "stale" } | { kind: "play" } | { kind: "next"; index: number } | { kind: "end" };

/**
 * What to do with a playback position while segment `index` is playing. Right after a seek,
 * iOS can still report the old position; anything well outside the segment is ignored so a
 * stale report cannot end a line early.
 */
export function segmentStep(segs: [number, number][], index: number, positionMs: number): SegmentStep {
  const startMs = segs[index][0] * 1000;
  const endMs = segs[index][1] * 1000;
  if (positionMs < startMs - 250 || positionMs > endMs + 1000) return { kind: "stale" };
  if (positionMs < endMs) return { kind: "play" };
  return index + 1 < segs.length ? { kind: "next", index: index + 1 } : { kind: "end" };
}

export function sourceLines(catalogue: DialectCatalogue, sourceIds: string[]): string[] {
  const wanted = new Set(sourceIds);
  return catalogue.lines.filter((l) => wanted.has(l.src)).map((l) => l.id);
}
