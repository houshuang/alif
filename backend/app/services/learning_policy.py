"""Feature switches and constants for the low-energy maintenance trial and the
novel sprint layered on top of it.

The maintenance experiment is a deliberately bundled policy: it keeps the
learner's normal card ceiling intact while spending that fixed budget on calmer
cards and more diagnostic long-gap reviews.  One master environment switch
provides an immediate, data-preserving rollback after a process restart.

The novel sprint (``novel_sprint_v1``, 2026-10-04) keeps every maintenance card
rule and changes only intake: a larger true-new budget, a shorter earned-practice
ladder on recovery days, and first priority for explicit ``reading_targets``.
It is active only while the maintenance package is also active, so switching the
sprint off returns exactly to ``low_energy_maintenance_v1``.
"""

from __future__ import annotations

import os


LOW_ENERGY_MAINTENANCE_ENV = "ALIF_LOW_ENERGY_MAINTENANCE_EXPERIMENT"
LOW_ENERGY_MAINTENANCE_VERSION = "low_energy_maintenance_v1"
LEGACY_DAILY_INTRO_CAP = 30
LOW_ENERGY_DAILY_INTRO_CAP = 2
MAX_DUE_WORDS_PER_SENTENCE_CARD = 4
TRIVIAL_COLLATERAL_RETRIEVABILITY = 0.97
CONFUSION_CONTEXT_WINDOW_DAYS = 14
CONFUSION_CONTEXT_MAX_SLOTS_PER_SESSION = 1

NOVEL_SPRINT_ENV = "ALIF_NOVEL_SPRINT"
NOVEL_SPRINT_VERSION = "novel_sprint_v1"
NOVEL_SPRINT_DAILY_INTRO_CAP = 8
# Recovery-day ladder under the sprint: (cards for any intro, cards for the full
# budget, mid budget). Maintenance uses (40, 100, 1); legacy (40, 100, 8).
NOVEL_SPRINT_RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO = 20
NOVEL_SPRINT_RECOVERY_MIN_SENTENCES_FOR_FULL_BUDGET = 60
NOVEL_SPRINT_RECOVERY_MID_INTRO_BUDGET = 4


def _env_on(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def low_energy_maintenance_enabled() -> bool:
    """Return whether the bounded maintenance experiment is active.

    The trial is default-on because it was explicitly authorized on 2026-09-03.
    Set ``ALIF_LOW_ENERGY_MAINTENANCE_EXPERIMENT=0`` and restart for the full
    legacy policy.  No stored state is rewritten by toggling the switch.
    """
    return _env_on(LOW_ENERGY_MAINTENANCE_ENV, "1")


def novel_sprint_enabled() -> bool:
    """Return whether the novel sprint intake policy is active.

    Default-off. ``ALIF_NOVEL_SPRINT=1`` activates it, but only on top of the
    maintenance package: with maintenance disabled the legacy ladder applies and
    the sprint switch is ignored.
    """
    return low_energy_maintenance_enabled() and _env_on(NOVEL_SPRINT_ENV, "0")


def active_daily_intro_cap() -> int:
    if novel_sprint_enabled():
        return NOVEL_SPRINT_DAILY_INTRO_CAP
    if low_energy_maintenance_enabled():
        return LOW_ENERGY_DAILY_INTRO_CAP
    return LEGACY_DAILY_INTRO_CAP


def active_recovery_ladder() -> tuple[int, int, int]:
    """Return (min cards for any intro, min cards for full budget, mid budget)."""
    if novel_sprint_enabled():
        return (
            NOVEL_SPRINT_RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO,
            NOVEL_SPRINT_RECOVERY_MIN_SENTENCES_FOR_FULL_BUDGET,
            NOVEL_SPRINT_RECOVERY_MID_INTRO_BUDGET,
        )
    if low_energy_maintenance_enabled():
        return (40, 100, 1)
    return (40, 100, 8)


def active_learning_policy_version() -> str:
    if novel_sprint_enabled():
        return NOVEL_SPRINT_VERSION
    if low_energy_maintenance_enabled():
        return LOW_ENERGY_MAINTENANCE_VERSION
    return "legacy"
