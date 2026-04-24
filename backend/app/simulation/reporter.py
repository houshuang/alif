"""Output formatting for simulation results."""

import csv
from dataclasses import asdict

from app.simulation.runner import DaySnapshot
from app.simulation.student import StudentProfile


def print_console_report(snapshots: list[DaySnapshot], profile_name: str = "") -> None:
    """Print an ASCII table of day-by-day simulation results."""
    print()
    print("=" * 90)
    title = "SIMULATION RESULTS"
    if profile_name:
        title += f" — {profile_name}"
    print(title)
    print("=" * 90)

    # Summary
    active_days = [s for s in snapshots if not s.skipped]
    total_reviews = sum(s.reviews_submitted for s in snapshots)
    total_sessions = sum(s.num_sessions for s in snapshots)
    total_graduated = sum(s.graduated_today for s in snapshots)
    total_leeches = sum(s.leeches_detected for s in snapshots)
    total_introduced = sum(s.auto_introduced for s in snapshots)

    print(f"\nDuration: {len(snapshots)} days ({len(active_days)} active, "
          f"{len(snapshots) - len(active_days)} skipped)")
    print(f"Total sessions: {total_sessions}")
    print(f"Total reviews: {total_reviews}")
    print(f"Total auto-introduced: {total_introduced}")
    print(f"Total graduated (acq->FSRS): {total_graduated}")
    print(f"Total leeches detected: {total_leeches}")
    if active_days:
        print(f"Avg sessions/active day: {total_sessions / len(active_days):.1f}")
        print(f"Avg reviews/active day: {total_reviews / len(active_days):.1f}")

    # Final state
    final = snapshots[-1]
    print(f"\nFinal word states:")
    for label, count in [
        ("Encountered", final.encountered),
        ("Acquiring", final.acquiring),
        ("Learning", final.learning),
        ("Known", final.known),
        ("Lapsed", final.lapsed),
        ("Suspended", final.suspended),
    ]:
        if count > 0:
            bar = "#" * min(count, 50)
            print(f"  {label:12s} {count:4d} {bar}")

    if final.acquiring > 0:
        print(f"\n  Acquisition boxes: B1={final.box_1} B2={final.box_2} B3={final.box_3}")

    # Day-by-day table
    print()
    print("-" * 90)
    header = f"{'Day':>4} {'Date':>12} {'Ses':>3} {'Due':>4} {'Rev':>4} {'New':>3} {'Und':>3} {'Par':>3} {'Nid':>3} {'Acq':>4} {'Lrn':>4} {'Knw':>4} {'Lap':>4}"
    print(header)
    print("-" * 95)

    for s in snapshots:
        if s.skipped:
            print(f"{s.day:4d} {s.date:>12}  SKIP"
                  f"{'':>30}{s.acquiring:4d} {s.learning:4d} {s.known:4d} {s.lapsed:4d}")
        else:
            print(
                f"{s.day:4d} {s.date:>12} {s.num_sessions:3d} {s.total_due:4d} {s.reviews_submitted:4d} "
                f"{s.auto_introduced:3d} {s.understood:3d} {s.partial:3d} {s.no_idea:3d} "
                f"{s.acquiring:4d} {s.learning:4d} {s.known:4d} {s.lapsed:4d}"
            )

    # Issue detection
    issues = detect_issues(snapshots)
    if issues:
        print()
        print("-" * 90)
        print("DETECTED ISSUES")
        print("-" * 90)
        for issue in issues:
            print(f"  * {issue}")
    else:
        print("\n  No issues detected.")

    print()


def detect_issues(snapshots: list[DaySnapshot]) -> list[str]:
    """Flag potential algorithm problems."""
    issues = []

    for s in snapshots:
        if s.total_due > 60 and not s.skipped:
            issues.append(f"Day {s.day}: Review avalanche — {s.total_due} words due")

    for s in snapshots:
        if s.acquiring > 40:
            issues.append(
                f"Day {s.day}: Acquisition bottleneck — {s.acquiring} words stuck in acquisition"
            )
            break

    total_leeches = sum(s.leeches_detected for s in snapshots)
    if total_leeches > 10:
        issues.append(f"High leech rate: {total_leeches} words became leeches")

    # Check for stagnation: no graduation for 10+ active days
    active_streak_no_grad = 0
    for s in snapshots:
        if s.skipped:
            continue
        if s.graduated_today == 0:
            active_streak_no_grad += 1
        else:
            active_streak_no_grad = 0
        if active_streak_no_grad >= 10:
            issues.append(
                f"Day {s.day}: Stagnation — no graduations in 10+ active days"
            )
            break

    return issues


def write_csv_report(snapshots: list[DaySnapshot], path: str) -> None:
    """Write all snapshot fields to a CSV file."""
    if not snapshots:
        return
    fieldnames = list(asdict(snapshots[0]).keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for snap in snapshots:
            writer.writerow(asdict(snap))
    print(f"CSV written to {path}")
