#!/usr/bin/env python3
"""Summarize volume-sweep runs into a comparison table."""
import json
import sys
from pathlib import Path


def summarize(path: Path) -> dict:
    run = json.loads(path.read_text())
    v = run["variant"]
    days = run["days"]
    study_days = [d for d in days if not d["skipped"]]
    cps = {c["day"]: c for c in run["checkpoints"]}

    def known_at(day):
        c = cps.get(day)
        return c["counts"].get("known", 0) if c else None

    known0 = known_at(0)
    intros = [d["auto_introduced"] for d in study_days]
    first_intro_day = next((d["day"] for d in days if d["auto_introduced"] > 0), None)
    # sustained: first day after which 7-day rolling intro sum >= 10
    name = f"v{v['volume']}"
    if v["schedule"] != "all":
        name += "_5d"
    if v["break"][0]:
        name += "_break"
    return {
        "name": name,
        "volume": v["volume"],
        "study_days": len(study_days),
        "cards_day_avg": round(sum(d["reviews_submitted"] for d in study_days) / max(len(study_days), 1), 1),
        "known_0": known0,
        "known_30": known_at(30),
        "known_60": known_at(60),
        "known_90": known_at(90),
        "d_known_90": (known_at(90) or 0) - (known0 or 0),
        "acq_90": cps.get(90, {}).get("counts", {}).get("acquiring", 0),
        "learn_90": cps.get(90, {}).get("counts", {}).get("learning", 0),
        "intros_total": sum(intros),
        "first_intro_day": first_intro_day,
        "graduated_total": sum(d["graduated_today"] for d in study_days),
        "leeches_total": sum(d["leeches_detected"] for d in study_days),
        "understood_pct": round(
            100 * sum(d["understood"] for d in study_days)
            / max(sum(d["understood"] + d["partial"] + d["no_idea"] for d in study_days), 1), 1),
        "due_end": days[-1]["total_due"],
        "box1_end": days[-1]["box_1"],
    }


def main():
    rows = [summarize(Path(p)) for p in sys.argv[1:]]
    rows.sort(key=lambda r: (r["volume"], r["name"]))
    cols = ["name", "study_days", "cards_day_avg", "known_0", "known_30", "known_60",
            "known_90", "d_known_90", "acq_90", "learn_90", "intros_total",
            "first_intro_day", "graduated_total", "leeches_total", "understood_pct",
            "due_end", "box1_end"]
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(r[c]) for c in cols))


if __name__ == "__main__":
    main()
