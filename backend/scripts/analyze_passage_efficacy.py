"""Track 4: passage card efficacy — is the long-passage experiment working?

Pulls card_shown events from the interaction logs, separates passage cards from
single-sentence cards, and looks at:
  - volume (how much data exists)
  - reading speed (response_ms / arabic-word count)
  - rate of correct comprehension answers
  - per-FSRS-stability bucket comparison so we're not comparing apples to oranges

Read-only. Run on the production VM (the logs live there).
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from glob import glob
from pathlib import Path

LOG_DIR = Path("/opt/alif/backend/data/logs")
DB_PATH = "/opt/alif/backend/data/alif.db"
SINCE_DAYS = 7  # window for analysis


def load_events() -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(days=SINCE_DAYS)
    events: list[dict] = []
    for p in sorted(glob(str(LOG_DIR / "interactions_*.jsonl"))):
        try:
            stem = Path(p).stem  # interactions_YYYY-MM-DD
            date_str = stem.split("_", 1)[1]
            file_date = datetime.fromisoformat(date_str)
            if file_date < cutoff - timedelta(days=1):
                continue
        except Exception:
            pass
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ts_raw = ev.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", ""))
                except Exception:
                    continue
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                if ts < cutoff:
                    continue
                events.append(ev)
    return events


def main() -> None:
    events = load_events()
    print(f"Loaded {len(events)} events in the last {SINCE_DAYS} days")

    # card_shown gives us the inventory; sentence_review gives outcomes.
    shown = [e for e in events if e.get("event") == "card_shown"]
    reviews = [e for e in events if e.get("event") == "sentence_review"]
    print(f"  card_shown:      {len(shown)}")
    print(f"  sentence_review: {len(reviews)}")

    # Card-type breakdown
    type_counts = Counter(e.get("card_type") for e in shown)
    print()
    print("=== Card-type mix ===")
    for ct, n in type_counts.most_common():
        print(f"  {ct or '<missing>':>12}: {n}")

    # Build sentence_id -> card_type map from shown events (latest wins)
    sentence_id_to_card_type: dict[int, str] = {}
    for e in shown:
        sid = e.get("sentence_id")
        ct = e.get("card_type")
        if isinstance(sid, int) and ct:
            sentence_id_to_card_type[sid] = ct

    # Lookup arabic word count + lemma stability for each sentence (for normalisation)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    cur = conn.cursor()

    # word count per sentence
    sentence_word_counts: dict[int, int] = {}
    if sentence_id_to_card_type:
        ids = list(sentence_id_to_card_type.keys())
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ",".join("?" for _ in chunk)
            for sid, n in cur.execute(
                f"SELECT sentence_id, COUNT(*) FROM sentence_words "
                f"WHERE sentence_id IN ({placeholders}) GROUP BY sentence_id",
                chunk,
            ).fetchall():
                sentence_word_counts[sid] = n

    # Per-review tally bucketed by card_type
    by_type_ms: dict[str, list[float]] = defaultdict(list)
    by_type_correct: dict[str, list[bool]] = defaultdict(list)

    for r in reviews:
        sid = r.get("sentence_id")
        if not isinstance(sid, int):
            continue
        ct = sentence_id_to_card_type.get(sid, "<unknown>")
        ms = r.get("response_ms")
        if isinstance(ms, (int, float)) and ms > 0:
            wc = sentence_word_counts.get(sid)
            if wc and wc > 0:
                # ms per word so we compare like for like
                by_type_ms[ct].append(ms / wc)
        comp = r.get("comprehension")
        if comp in {"understood", "partial", "no_idea"}:
            by_type_correct[ct].append(comp == "understood")

    print()
    print("=== Reading speed (ms per word, lower = faster) ===")
    for ct in sorted(by_type_ms.keys()):
        samples = by_type_ms[ct]
        if len(samples) < 5:
            print(f"  {ct:>12}: n={len(samples)} (too few to summarise)")
            continue
        med = statistics.median(samples)
        p25, p75 = statistics.quantiles(samples, n=4)[0], statistics.quantiles(samples, n=4)[2]
        print(f"  {ct:>12}: n={len(samples):>4} median={med:7.0f}  p25={p25:7.0f}  p75={p75:7.0f}")

    print()
    print("=== Comprehension (% 'understood') ===")
    for ct in sorted(by_type_correct.keys()):
        outcomes = by_type_correct[ct]
        if not outcomes:
            continue
        pct = 100 * sum(outcomes) / len(outcomes)
        print(f"  {ct:>12}: n={len(outcomes):>4} understood={pct:5.1f}%")

    # Specifically passage vs sentence summary
    print()
    print("=== Passage vs single-sentence (head-to-head) ===")
    p_ms = by_type_ms.get("passage", [])
    s_ms = by_type_ms.get("sentence", [])
    if p_ms and s_ms:
        pmed = statistics.median(p_ms)
        smed = statistics.median(s_ms)
        ratio = pmed / smed if smed else 0
        print(f"  median ms/word — passage: {pmed:.0f}   sentence: {smed:.0f}   ratio: {ratio:.2f}")
    else:
        print(f"  insufficient data: passage n={len(p_ms)}  sentence n={len(s_ms)}")
    p_ok = by_type_correct.get("passage", [])
    s_ok = by_type_correct.get("sentence", [])
    if p_ok and s_ok:
        ppct = 100 * sum(p_ok) / len(p_ok)
        spct = 100 * sum(s_ok) / len(s_ok)
        print(f"  comprehension — passage: {ppct:.1f}%   sentence: {spct:.1f}%")

    conn.close()


if __name__ == "__main__":
    main()
