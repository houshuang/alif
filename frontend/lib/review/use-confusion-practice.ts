import { useEffect, useRef, useState } from "react";
import { ConfusionLibrary, ConfusionPair, UnresolvedConfusion, loadPracticeDone, readyPairs, savePracticeDone } from "../confusion-learning";
import { listConfusions, prepareConfusion, refreshConfusions } from "../api";

type ActivePractice = {pair: ConfusionPair; start: number; showLesson: boolean};

/** Prepare in the background; present only at a sentence submission boundary.
 * The server decides which pairs are due (spaced runs, a daily limit on new pairs); each due
 * pair appears at most once per session as one two-question run. */
export function useConfusionPractice(sessionId: string | null, enabled: boolean) {
  const [active, setActive] = useState<ActivePractice | null>(null);
  const [matching, setMatching] = useState<UnresolvedConfusion | null>(null);
  const asked = useRef(new Set<number>());
  const library = useRef<ConfusionLibrary | null>(null);
  const since = useRef(0), generation = useRef(0);
  const done = useRef<Record<string, number>>({});
  const shown = useRef(new Set<number>());
  const busy = useRef(false), polling = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prepared = useRef(new Set<number>());
  async function refresh(token: number) {
    if (busy.current) return;
    busy.current = true;
    try {
      const data = await refreshConfusions();
      if (token !== generation.current) return;
      library.current = data;
      const ready = readyPairs(data, done.current, shown.current).length > 0;
      const canPrepare = (p: ConfusionPair) => !p.lesson && p.due_now && p.generation_state !== "failed" && !prepared.current.has(p.id);
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
    setActive(null); setMatching(null); asked.current.clear(); library.current = null; since.current = 0; busy.current = false;
    prepared.current.clear(); shown.current.clear();
    if (sessionId && enabled) void loadPracticeDone().then(saved => {
      if (token !== generation.current) return;
      done.current = saved;
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
      const ready = readyPairs(library.current, done.current, shown.current);
      const pair = ready.find(p => p.last_session_id === sessionId) ?? ready[0];
      if (pair) {
        shown.current.add(pair.id);
        setActive({pair: {...pair, offline: library.current?.offline}, start: pair.practice_start ?? 0, showLesson: (pair.practice_step ?? 0) === 0});
        since.current = 0;
      }
    }
    void refresh(generation.current);
  }
  function rest() {
    // Finishing or closing rests the pair locally too, so offline or before the server catches up it stays away.
    if (!active) return;
    const at = Date.now();
    done.current = {...done.current, [active.pair.id]: at};
    void savePracticeDone(active.pair.id, at);
  }
  function complete(_next: number) {
    rest();
    setActive(null); since.current = 0;
    void refresh(generation.current);
  }
  function skip() {
    // Closing scores nothing; the pair rests like a finished run.
    rest();
    setActive(null); since.current = 0;
  }
  return {active, matching, submitted, complete, skip, matchSaved: () => { setMatching(null); void refresh(generation.current); }, skipMatch: () => setMatching(null)};
}
