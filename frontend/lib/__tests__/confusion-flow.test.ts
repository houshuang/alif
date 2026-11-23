import { readFileSync } from "fs";
import path from "path";
import ts from "typescript";

// Exercise the real screen effects without mounting Expo's native navigation stack.
const source = readFileSync(path.resolve(__dirname, "../../app/index.tsx"), "utf8");
const tree = ts.createSourceFile("index.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function screenEffect(hook: string, marker: string) {
  const matches: ts.CallExpression[] = [];
  function visit(node: ts.Node) {
    if (ts.isCallExpression(node) && node.expression.getText(tree) === hook &&
        node.arguments[0]?.getText(tree).includes(marker)) matches.push(node);
    ts.forEachChild(node, visit);
  }
  visit(tree);
  if (matches.length !== 1) throw new Error(`Expected one ${hook} with ${marker}`);
  const call = matches[0];
  const run = (state: Record<string, unknown>) => {
    const callback = ts.transpileModule(`const effect = ${call.arguments[0].getText(tree)};`, {
      compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
    }).outputText;
    return new Function(...Object.keys(state), `${callback}; return effect();`)(...Object.values(state));
  };
  return Object.assign(run, { dependencies: call.arguments[1].getText(tree) });
}
const checkWords = screenEffect("useLayoutEffect", "maybeShowFollowup()");
const continueBatch = screenEffect("useEffect", "continuingRef.current = true");
function boundaryState() {
  return {
    results: {total: 10}, totalCards: 10, inWrapUp: false, sessionStopped: false,
    loading: false, submittingReview: false, continuingRef: {current: false},
    confusionPractice: {active: {pair: {id: 54}}, matching: null},
    mode: "reading", TODAY_CARD_MIN_BATCH: 10, sentenceSession: {session_id: "old"},
    setTodayCard: jest.fn(), logCardShown: jest.fn(), flushQueue: jest.fn(async () => {}),
    getTodayProgress: jest.fn(async () => ({})), dropCachedSession: jest.fn(async () => {}),
    loadSession: jest.fn(async () => {}),
  };
}

describe("confusion practice owns its sentence boundary", () => {
  it("keeps the current session until the confusion lesson is finished or dismissed", async () => {
    // 23 Nov report: a word card displaced the lesson and it vanished on return.
    const state = boundaryState();
    continueBatch(state);
    await Promise.resolve();
    expect(state.loadSession).not.toHaveBeenCalled();
    expect(state.dropCachedSession).not.toHaveBeenCalled();
    expect(state.setTodayCard).not.toHaveBeenCalled();
    expect(state.continuingRef.current).toBe(false);
  });

  it("does not start a word review over the confusion card", () => {
    const state = {...boundaryState(), results: {total: 2}, cardIndex: 2,
      todayCard: null, maybeShowFollowup: jest.fn(() => false), maybeFireCheckpoint: jest.fn()};
    checkWords(state);
    expect(state.maybeShowFollowup).not.toHaveBeenCalled();
    expect(state.maybeFireCheckpoint).not.toHaveBeenCalled();
  });

  it("also holds word checks and batch rollover during manual confusor matching", () => {
    const state = {...boundaryState(), cardIndex: 2, todayCard: null,
      confusionPractice: {active: null, matching: {capture_id: 17}},
      maybeShowFollowup: jest.fn(() => false), maybeFireCheckpoint: jest.fn()};
    continueBatch(state);
    checkWords({...state, results: {total: 2}});
    expect(state.dropCachedSession).not.toHaveBeenCalled();
    expect(state.maybeShowFollowup).not.toHaveBeenCalled();
    expect(state.maybeFireCheckpoint).not.toHaveBeenCalled();
  });

  it("reconsiders a due word check after the confusion card closes", () => {
    const state = {...boundaryState(), results: {total: 2}, cardIndex: 2, todayCard: null,
      maybeShowFollowup: jest.fn(() => false), maybeFireCheckpoint: jest.fn()};
    checkWords(state);
    checkWords({...state, confusionPractice: {active: null, matching: null}});
    expect(state.maybeShowFollowup).toHaveBeenCalledTimes(1);
    expect(state.maybeFireCheckpoint).toHaveBeenCalledTimes(1);
  });

  it("continues the batch once after the confusion card closes", async () => {
    const state = boundaryState();
    continueBatch(state);
    continueBatch({...state, confusionPractice: {active: null, matching: null}});
    await new Promise(setImmediate);
    expect(state.dropCachedSession).toHaveBeenCalledTimes(1);
    expect(state.loadSession).toHaveBeenCalledTimes(1);
    expect(state.setTodayCard).toHaveBeenCalledTimes(2); // loading, then progress
  });

  it("reacts to both practice and matching closing without needing another sentence", () => {
    for (const effect of [continueBatch, checkWords]) {
      expect(effect.dependencies).toContain("confusionPractice.active");
      expect(effect.dependencies).toContain("confusionPractice.matching");
    }
  });
});
