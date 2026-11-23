import { useEffect, useRef, useState } from "react";
import { ConfusionLibrary, ConfusionPair, UnresolvedConfusion, loadPracticeTurns, savePracticeTurn } from "../confusion-learning";
import { listConfusions, prepareConfusion, refreshConfusions } from "../api";

/** Prepare in the background; present only at a sentence submission boundary. */
export function useConfusionPractice(sessionId: string | null, enabled: boolean) {
  const [active, setActive] = useState<{pair: ConfusionPair; start: number} | null>(null);
  const [matching, setMatching] = useState<UnresolvedConfusion | null>(null);
  const asked = useRef(new Set<number>());
  const library = useRef<ConfusionLibrary | null>(null);
  const since = useRef(0), generation = useRef(0);
  const turns = useRef<Record<string, {next: number; lastReport: string | null}>>({});
  const busy = useRef(false), polling = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prepared = useRef(new Set<number>());
  async function refresh(token: number) {
    if (busy.current) return;
    busy.current = true;
    try {
      const data = await refreshConfusions();
      if (token !== generation.current) return;
      library.current = data;
      const ready = data.pairs.some(p => p.lesson && p.status !== "resolved" && (turns.current[p.id]?.next ?? 0) < p.lesson.questions.length);
      const canPrepare = (p: ConfusionPair) => !p.lesson && p.generation_state !== "failed" && !prepared.current.has(p.id);
      const fresh = data.pairs.find(p => canPrepare(p) && p.last_session_id === sessionId);
      const pending = fresh ?? (!ready ? data.pairs.find(canPrepare) : undefined);
      if (!data.offline && pending) {
        prepared.current.add(pending.id);
        await prepareConfusion(pending.id);
        const poll = async () => {
          if (token !== generation.current) return;
          try {
            const next = await listConfusions();
            if (token !== generation.current) return;
            library.current = next;
            const pair = next.pairs.find(p => p.id === pending.id);
            if (pair?.generation_state === "working") polling.current = setTimeout(poll, 3000);
          } catch { /* Reading continues; the library screen offers retry. */ }
        };
        polling.current = setTimeout(poll, 3000);
      }
    } catch { /* Offline without a cache: continue normal reading. */ }
    finally { if (token === generation.current) busy.current = false; }
  }
  useEffect(() => {
    const token = ++generation.current;
    setActive(null); setMatching(null); asked.current.clear(); library.current = null; since.current = 0; busy.current = false; prepared.current.clear();
    if (sessionId && enabled) void loadPracticeTurns().then(saved => {
      if (token !== generation.current) return;
      turns.current = Object.fromEntries(Object.entries(saved).map(([key, v]) => [key, {...v, next: v.next >= 6 ? 0 : v.next}]));
      void refresh(token);
    });
    return () => { generation.current++; if (polling.current) clearTimeout(polling.current); };
  }, [sessionId, enabled]);
  function submitted() {
    if (!enabled || !sessionId) return;
    since.current++;
    const needsMatch = library.current?.unresolved.find(c => c.state === "needs_confirmation" && c.session_id === sessionId && !asked.current.has(c.capture_id));
    if (!active && !matching && needsMatch) {
      asked.current.add(needsMatch.capture_id); setMatching(needsMatch);
      void refresh(generation.current); return;
    }
    if (since.current >= 2 && !active && !matching) {
      const ready = library.current?.pairs.filter(p => p.lesson && p.status !== "resolved" &&
        ((turns.current[p.id]?.lastReport === p.last_report ? turns.current[p.id]?.next : 0) ?? 0) < p.lesson.questions.length) ?? [];
      const pair = ready.find(p => p.last_session_id === sessionId) ?? ready[0];
      if (pair) {
        const start = turns.current[pair.id]?.lastReport === pair.last_report ? turns.current[pair.id]?.next ?? 0 : 0;
        setActive({pair: {...pair, offline: library.current?.offline}, start}); since.current = 0;
      }
    }
    void refresh(generation.current);
  }
  function complete(next: number) {
    if (active) {
      turns.current[active.pair.id] = {next, lastReport: active.pair.last_report};
      void savePracticeTurn(active.pair, next);
    }
    setActive(null); since.current = 0;
    void refresh(generation.current);
  }
  function skip() {
    // Skip this pair for the rest of this session, without a failure or mastery claim.
    if (active) turns.current[active.pair.id] = {next: active.pair.lesson!.questions.length, lastReport: active.pair.last_report};
    setActive(null); since.current = 0;
  }
  return {active, matching, submitted, complete, skip, matchSaved: () => { setMatching(null); void refresh(generation.current); }, skipMatch: () => setMatching(null)};
}
