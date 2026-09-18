"""Fire-and-forget daemon threads for provider-bound enrichment work.

Enrichment, root/pattern notes and memory hooks call LLM providers from a
daemon thread so the request that triggered them returns immediately. Such a
thread can outlive the request, and in tests it outlives the test and its
database. The test suite sets ``ENABLED = False`` (see tests/conftest.py),
the same way it turns FastAPI ``BackgroundTasks.add_task`` into a no-op;
tests that need the work call the service directly.
"""

import threading
from typing import Any, Callable

ENABLED = True


def start_daemon(target: Callable[..., Any], *args: Any, name: str | None = None) -> bool:
    """Start ``target(*args)`` on a daemon thread. Returns False when disabled."""
    if not ENABLED:
        return False
    threading.Thread(target=target, args=args, daemon=True, name=name).start()
    return True
