"""Feature switches and constants for the low-energy maintenance trial.

The experiment is a deliberately bundled policy: it keeps the learner's normal
card ceiling intact while spending that fixed budget on calmer cards and more
diagnostic long-gap reviews.  One master environment switch provides an
immediate, data-preserving rollback after a process restart.
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


def low_energy_maintenance_enabled() -> bool:
    """Return whether the bounded maintenance experiment is active.

    The trial is default-on because it was explicitly authorized on 2026-09-03.
    Set ``ALIF_LOW_ENERGY_MAINTENANCE_EXPERIMENT=0`` and restart for the full
    legacy policy.  No stored state is rewritten by toggling the switch.
    """
    return os.environ.get(LOW_ENERGY_MAINTENANCE_ENV, "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def active_daily_intro_cap() -> int:
    return (
        LOW_ENERGY_DAILY_INTRO_CAP
        if low_energy_maintenance_enabled()
        else LEGACY_DAILY_INTRO_CAP
    )


def active_learning_policy_version() -> str:
    return (
        LOW_ENERGY_MAINTENANCE_VERSION
        if low_energy_maintenance_enabled()
        else "legacy"
    )
