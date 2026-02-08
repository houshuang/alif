#!/usr/bin/env python3
"""
Standalone simulation of FSRS spaced repetition for the Alif Arabic learning app.

Simulates 30-90 days of usage with configurable patterns and outputs
day-by-day statistics with text-based visualizations.

Usage:
    python scripts/simulate_usage.py --days 60 --words-per-day 10 --accuracy 0.8
    python scripts/simulate_usage.py --days 30 --words-per-day 5 --accuracy 0.9 --retention 0.85
    python scripts/simulate_usage.py --preset intensive
"""

import argparse
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import fsrs

Rating = fsrs.Rating
State = fsrs.State


@dataclass
class SimCard:
    card: fsrs.Card
    lemma_id: int
    word: str


@dataclass
class DayStats:
    day: int
    date: str
    reviews_done: int = 0
    new_words_learned: int = 0
    cards_due: int = 0
    skipped: bool = False
    ratings: dict = field(default_factory=lambda: {"Again": 0, "Hard": 0, "Good": 0, "Easy": 0})
    states: dict = field(default_factory=lambda: {"Learning": 0, "Review": 0, "Relearning": 0})
    avg_stability: float = 0.0
    total_cards: int = 0
    session_minutes: int = 0


def choose_rating(accuracy: float) -> Rating:
    r = random.random()
    if r < (1 - accuracy) * 0.7:
        return Rating.Again
    elif r < (1 - accuracy):
        return Rating.Hard
    elif r < (1 - accuracy) + (accuracy * 0.7):
        return Rating.Good
    else:
        return Rating.Easy


SAMPLE_WORDS = [
    "kitab", "madrasa", "kalb", "bait", "walad", "bint", "rajul", "imra'a",
    "shams", "qamar", "ma'", "ta'am", "sayara", "tariq", "masjid", "souq",
    "yawm", "layl", "sabah", "masa'", "ustadh", "talib", "lugha", "arabiyya",
    "qahwa", "shai", "khubz", "labn", "samak", "dajaj", "lahm", "fawakeh",
    "khadra", "hamra'", "bayda'", "sawda'", "kabir", "saghir", "jadid", "qadim",
    "jamil", "qabih", "sa'id", "hazin", "sari'", "bati'", "ba'id", "qarib",
    "fawq", "taht", "amam", "wara'", "yamin", "yasar", "sharq", "gharb",
    "shamal", "janub", "huna", "hunak", "mata", "ayna", "kayfa", "limadha",
    "na'am", "la", "min fadlak", "shukran", "ahlan", "ma'a salama",
    "wahid", "ithnan", "thalatha", "arba'a", "khamsa", "sitta", "sab'a",
    "thamaniya", "tis'a", "ashara", "mi'a", "alf", "darasa", "kataba",
    "qara'a", "sami'a", "takallama", "dhahaba", "ja'a", "akala", "shariba",
    "nama", "istayqadha", "ishtara", "ba'a", "ahabba", "kariha", "arida",
    "fahima", "sa'ala", "ajaba", "intadhara", "wajada", "faqada",
]


def run_simulation(
    days: int,
    words_per_day: int,
    accuracy: float,
    session_minutes: int,
    skip_probability: float,
    desired_retention: float,
    seed: int | None,
    schedule_preset: str | None,
) -> tuple[list[DayStats], list[dict]]:
    if seed is not None:
        random.seed(seed)

    scheduler = fsrs.Scheduler(desired_retention=desired_retention, enable_fuzzing=False)
    start = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)

    all_cards: list[SimCard] = []
    word_idx = 0
    stats: list[DayStats] = []
    logs: list[dict] = []

    daily_schedule: dict[int, dict | None] = {}
    if schedule_preset == "intensive":
        for d in range(1, days + 1):
            daily_schedule[d] = {"minutes": 60, "new": 15}
    elif schedule_preset == "casual":
        for d in range(1, days + 1):
            if d % 3 == 0:
                daily_schedule[d] = None
            else:
                daily_schedule[d] = {"minutes": 15, "new": 3}
    elif schedule_preset == "realistic":
        for d in range(1, days + 1):
            week = (d - 1) // 7
            day_of_week = (d - 1) % 7
            if week % 3 == 2:
                daily_schedule[d] = None
            elif day_of_week in (5, 6):
                daily_schedule[d] = {"minutes": 45, "new": 10}
            elif day_of_week == 0:
                daily_schedule[d] = {"minutes": 30, "new": 5}
            else:
                daily_schedule[d] = {"minutes": 20, "new": words_per_day}
    else:
        for d in range(1, days + 1):
            if random.random() < skip_probability:
                daily_schedule[d] = None
            else:
                daily_schedule[d] = {"minutes": session_minutes, "new": words_per_day}

    for day in range(1, days + 1):
        day_start = start + timedelta(days=day - 1)
        session_id = str(uuid.uuid4())

        day_info = daily_schedule.get(day)
        ds = DayStats(day=day, date=day_start.strftime("%Y-%m-%d"))

        ds.cards_due = sum(1 for sc in all_cards if sc.card.due <= day_start)
        ds.total_cards = len(all_cards)

        if day_info is None:
            ds.skipped = True
            for sc in all_cards:
                state_name = sc.card.state.name
                if state_name in ds.states:
                    ds.states[state_name] += 1
            stats.append(ds)
            continue

        ds.session_minutes = day_info["minutes"]
        minute = 0

        due_cards = [(i, sc) for i, sc in enumerate(all_cards) if sc.card.due <= day_start]
        due_cards.sort(key=lambda x: x[1].card.due)

        for idx, sc in due_cards:
            if minute >= day_info["minutes"]:
                break

            t = day_start + timedelta(minutes=minute)
            rating = choose_rating(accuracy)
            sc.card, log = scheduler.review_card(sc.card, rating, t)
            ds.reviews_done += 1
            ds.ratings[rating.name] += 1
            minute += 1

            logs.append({
                "ts": t.isoformat(),
                "event": "review",
                "lemma_id": sc.lemma_id,
                "word": sc.word,
                "rating": rating.value,
                "response_ms": random.randint(1500, 5000),
                "session_id": session_id,
            })

        new_count = min(day_info["new"], (day_info["minutes"] - minute) // 2)
        for i in range(new_count):
            if word_idx >= len(SAMPLE_WORDS):
                break
            t = day_start + timedelta(minutes=minute)
            card = fsrs.Card()
            rating = choose_rating(min(accuracy + 0.1, 1.0))
            card, _ = scheduler.review_card(card, rating, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)

            word = SAMPLE_WORDS[word_idx]
            sc = SimCard(card=card, lemma_id=word_idx, word=word)
            all_cards.append(sc)
            word_idx += 1
            ds.new_words_learned += 1
            minute += 2

            logs.append({
                "ts": t.isoformat(),
                "event": "new_word",
                "lemma_id": sc.lemma_id,
                "word": word,
                "rating": rating.value,
                "response_ms": random.randint(3000, 8000),
                "session_id": session_id,
            })

        for sc in all_cards:
            state_name = sc.card.state.name
            if state_name in ds.states:
                ds.states[state_name] += 1

        stabilities = [sc.card.stability for sc in all_cards if sc.card.stability]
        ds.avg_stability = sum(stabilities) / len(stabilities) if stabilities else 0

        stats.append(ds)

    return stats, logs


def print_summary(stats: list[DayStats]) -> None:
    print("\n" + "=" * 80)
    print("ALIF FSRS SIMULATION RESULTS")
    print("=" * 80)

    total_reviews = sum(d.reviews_done for d in stats)
    total_new = sum(d.new_words_learned for d in stats)
    active_days = sum(1 for d in stats if not d.skipped)
    skip_days = sum(1 for d in stats if d.skipped)

    print(f"\nDuration: {len(stats)} days ({active_days} active, {skip_days} skipped)")
    print(f"Total reviews: {total_reviews}")
    print(f"Total new words: {total_new}")
    print(f"Avg reviews/active day: {total_reviews / max(active_days, 1):.1f}")

    final = stats[-1]
    print(f"\nFinal card states (total {final.total_cards}):")
    for state, count in final.states.items():
        if count > 0:
            bar = "#" * min(count, 50)
            print(f"  {state:12s} {count:4d} {bar}")

    if final.total_cards > 0 and final.avg_stability > 0:
        print(f"\nAvg stability: {final.avg_stability:.1f} days")


def print_review_load_chart(stats: list[DayStats]) -> None:
    print("\n" + "-" * 80)
    print("DAILY REVIEW LOAD")
    print("-" * 80)

    max_reviews = max((d.reviews_done for d in stats), default=1) or 1
    scale = 60

    print(f"\n{'Day':>4} {'Rev':>4} {'Due':>4} {'New':>3} Review load")
    print(f"{'':>4} {'':>4} {'':>4} {'':>3} {'|' + '-' * scale + '|'}")

    for d in stats:
        if d.skipped:
            rev_bar = ""
            marker = " SKIP"
        else:
            bar_len = int(d.reviews_done / max_reviews * scale) if max_reviews > 0 else 0
            rev_bar = "#" * bar_len
            marker = ""

        due_indicator = "!" if d.cards_due > 30 else ""
        print(f"{d.day:4d} {d.reviews_done:4d} {d.cards_due:4d} {d.new_words_learned:3d} |{rev_bar:<{scale}}|{marker}{due_indicator}")


def print_due_forecast(stats: list[DayStats]) -> None:
    print("\n" + "-" * 80)
    print("CARDS DUE PER DAY (forecast)")
    print("-" * 80)

    max_due = max((d.cards_due for d in stats), default=1) or 1
    scale = 60

    for d in stats:
        bar_len = int(d.cards_due / max_due * scale) if max_due > 0 else 0
        bar = "=" * bar_len
        skip = " [SKIP]" if d.skipped else ""
        print(f"{d.day:4d} {d.cards_due:4d} |{bar:<{scale}}|{skip}")


def detect_issues(stats: list[DayStats]) -> list[str]:
    issues = []

    for d in stats:
        if d.cards_due > 50 and not d.skipped:
            issues.append(f"Day {d.day}: Review avalanche -- {d.cards_due} cards due")

    for d in stats:
        if d.total_cards > 0:
            learning_pct = d.states.get("Learning", 0) / d.total_cards
            if learning_pct > 0.5 and d.day > 7:
                issues.append(f"Day {d.day}: {learning_pct:.0%} cards still in Learning state")

    max_backlog = 0
    for d in stats:
        if d.skipped and d.cards_due > max_backlog:
            max_backlog = d.cards_due
    if max_backlog > 30:
        issues.append(f"Max backlog during skip periods: {max_backlog} cards")

    return issues


def main():
    parser = argparse.ArgumentParser(description="Simulate FSRS spaced repetition usage")
    parser.add_argument("--days", type=int, default=30, help="Number of days to simulate")
    parser.add_argument("--words-per-day", type=int, default=5, help="New words per active day")
    parser.add_argument("--accuracy", type=float, default=0.8, help="Probability of correct recall (0-1)")
    parser.add_argument("--session-minutes", type=int, default=20, help="Session length in minutes")
    parser.add_argument("--skip-probability", type=float, default=0.2, help="Probability of skipping a day")
    parser.add_argument("--retention", type=float, default=0.9, help="FSRS desired retention (0-1)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--preset", choices=["intensive", "casual", "realistic"], help="Use a preset schedule")
    parser.add_argument("--json", action="store_true", help="Output stats as JSON")
    parser.add_argument("--logs", type=str, help="Write interaction logs to file (JSONL)")

    args = parser.parse_args()

    print(f"Simulating {args.days} days of Arabic learning with FSRS")
    print(f"  Words/day: {args.words_per_day}, Accuracy: {args.accuracy:.0%}")
    print(f"  Session: {args.session_minutes}min, Skip prob: {args.skip_probability:.0%}")
    print(f"  Desired retention: {args.retention}, Seed: {args.seed}")
    if args.preset:
        print(f"  Preset: {args.preset}")

    stats, logs = run_simulation(
        days=args.days,
        words_per_day=args.words_per_day,
        accuracy=args.accuracy,
        session_minutes=args.session_minutes,
        skip_probability=args.skip_probability,
        desired_retention=args.retention,
        seed=args.seed,
        schedule_preset=args.preset,
    )

    if args.json:
        output = []
        for d in stats:
            output.append({
                "day": d.day,
                "date": d.date,
                "reviews_done": d.reviews_done,
                "new_words_learned": d.new_words_learned,
                "cards_due": d.cards_due,
                "skipped": d.skipped,
                "ratings": d.ratings,
                "states": d.states,
                "avg_stability": round(d.avg_stability, 2),
                "total_cards": d.total_cards,
            })
        print(json.dumps(output, indent=2))
    else:
        print_summary(stats)
        print_review_load_chart(stats)
        print_due_forecast(stats)

        issues = detect_issues(stats)
        if issues:
            print("\n" + "-" * 80)
            print("DETECTED ISSUES")
            print("-" * 80)
            for issue in issues:
                print(f"  * {issue}")
        else:
            print("\n  No issues detected.")

    if args.logs:
        with open(args.logs, "w") as f:
            for entry in logs:
                f.write(json.dumps(entry) + "\n")
        print(f"\nWrote {len(logs)} log entries to {args.logs}")


if __name__ == "__main__":
    main()
