"""Student behavior profiles for simulation.

Calibrated against real usage data (Feb 2026):
- Real user does 6-18 short sessions/day (avg 4.2 sentences each)
- Almost zero "no_idea" ratings (<1%)
- Comprehension: ~61% understood, ~39% partial on new algorithm
- Studies every day, even lightly on some days
"""

import random
from dataclasses import dataclass


@dataclass
class StudentProfile:
    name: str
    base_comprehension: float  # baseline word understanding probability
    miss_probability: float  # chance of marking missed when struggling
    session_size_range: tuple[int, int]  # (min, max) sentences per session
    sessions_per_day_range: tuple[int, int]  # (min, max) sessions on study days
    study_days_per_week: float  # expected study days out of 7
    no_idea_threshold: float  # ratio below which sentence = "no_idea" (lower = rarer)

    def should_study_today(self, weekday: int, day_number: int) -> bool:
        """Decide whether the student studies on this day."""
        prob = self.study_days_per_week / 7.0
        return random.random() < prob

    def sessions_today(self) -> int:
        """How many sessions the student does on a study day."""
        return random.randint(*self.sessions_per_day_range)

    def session_size(self) -> int:
        """Pick a random session size within the profile range."""
        return random.randint(*self.session_size_range)

    def word_understood_probability(self, word: dict) -> float:
        """Probability this word is understood, based on its state and stability."""
        state = word.get("knowledge_state", "new")
        stability = word.get("stability") or 0.0

        if word.get("is_function_word"):
            return 0.95

        if state == "known" and stability > 10.0:
            return 0.95
        elif state == "known":
            return 0.85
        elif state == "learning":
            return 0.70 + (self.base_comprehension - 0.5) * 0.3
        elif state == "lapsed":
            return 0.55
        elif state == "acquiring":
            return self.base_comprehension * 0.8
        elif state == "encountered":
            return 0.40
        else:
            return 0.25

    def decide_comprehension(self, words: list[dict]) -> str:
        """Decide sentence-level comprehension from per-word probabilities."""
        content_words = [
            w for w in words
            if not w.get("is_function_word") and w.get("lemma_id")
        ]
        if not content_words:
            return "understood"

        understood_flags = [
            random.random() < self.word_understood_probability(w)
            for w in content_words
        ]
        ratio = sum(understood_flags) / len(understood_flags)

        if ratio >= 0.9:
            return "understood"
        elif ratio >= self.no_idea_threshold:
            return "partial"
        else:
            return "no_idea"

    def decide_missed_words(
        self, words: list[dict], comprehension: str
    ) -> tuple[list[int], list[int]]:
        """Pick which words are missed/confused for a 'partial' sentence.

        Returns (missed_lemma_ids, confused_lemma_ids).
        """
        if comprehension != "partial":
            return [], []

        missed = []
        confused = []
        for w in words:
            if w.get("is_function_word") or not w.get("lemma_id"):
                continue
            p = self.word_understood_probability(w)
            if random.random() >= p:
                if random.random() < self.miss_probability:
                    missed.append(w["lemma_id"])
                else:
                    confused.append(w["lemma_id"])
        return missed, confused


# Profiles calibrated against real usage patterns.
# Key insight: real user does many short sessions throughout the day,
# enabling same-day box progression (4h acquisition interval).

REALISTIC = StudentProfile(
    name="realistic",
    base_comprehension=0.75,
    miss_probability=0.25,
    session_size_range=(3, 8),
    sessions_per_day_range=(4, 8),
    study_days_per_week=6.5,
    no_idea_threshold=0.2,  # very rare no_idea
)

BEGINNER = StudentProfile(
    name="beginner",
    base_comprehension=0.55,
    miss_probability=0.40,
    session_size_range=(3, 6),
    sessions_per_day_range=(2, 4),
    study_days_per_week=5.0,
    no_idea_threshold=0.3,  # rare no_idea
)

STRONG = StudentProfile(
    name="strong",
    base_comprehension=0.85,
    miss_probability=0.10,
    session_size_range=(4, 10),
    sessions_per_day_range=(5, 10),
    study_days_per_week=6.5,
    no_idea_threshold=0.15,  # very rare no_idea
)

CASUAL = StudentProfile(
    name="casual",
    base_comprehension=0.70,
    miss_probability=0.25,
    session_size_range=(3, 5),
    sessions_per_day_range=(1, 3),
    study_days_per_week=3.5,
    no_idea_threshold=0.25,
)

INTENSIVE = StudentProfile(
    name="intensive",
    base_comprehension=0.75,
    miss_probability=0.20,
    session_size_range=(5, 12),
    sessions_per_day_range=(6, 12),
    study_days_per_week=7.0,
    no_idea_threshold=0.2,
)

# Calibrated from production data analysis (2026-02-20):
# - 13 active days, 14.2 sessions/day avg, 6.8 sentences/session median 5
# - 89.8% overall accuracy, 53.6% understood, 46.1% partial, 0.3% no_idea
# - Acquisition accuracy: 83%, FSRS accuracy: 93.3%
# - Retention improving: W06=78%, W07=90%, W08=97.5%
CALIBRATED = StudentProfile(
    name="calibrated",
    base_comprehension=0.80,
    miss_probability=0.20,
    session_size_range=(3, 10),
    sessions_per_day_range=(8, 18),
    study_days_per_week=7.0,
    no_idea_threshold=0.15,
)

PROFILES: dict[str, StudentProfile] = {
    "realistic": REALISTIC,
    "beginner": BEGINNER,
    "strong": STRONG,
    "casual": CASUAL,
    "intensive": INTENSIVE,
    "calibrated": CALIBRATED,
}
