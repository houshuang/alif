"""Walk episodes: feed, feedback, topic queue and the daily-generation decision.

Walk episodes are podcast files with ``format_type == "walk"`` written by
``scripts/generate_walk_episode.py``. This module holds the parts the API and
the daily job share. Nothing here touches lemmas, FSRS or acquisition.
"""

import json
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from app.services import podcast_service

RATINGS = ("easy", "right", "hard")
# A word may appear in an episode once its FSRS stability reaches this many
# days. "Too hard" moves one step up, "too easy" one step down.
FIRM_DAY_STEPS = (14, 30, 60, 120, 240)
DEFAULT_FIRM_DAYS = 30
# The daily job stops after this many episodes in a row with no completion and
# no feedback, so an unused feed cannot keep spending on speech.
MAX_UNACKNOWLEDGED = 4
SAMPLE_PREFIX = "walk-sample"

SEED_TOPICS = [
    "Ibn Fadlan's journey from Baghdad to the Vikings on the Volga",
    "Kalila wa Dimna: how an Indian book of animal stories travelled the world",
    "Al-Khwarizmi, algebra and the Indian numbers",
    "How the Arabic script got its dots and vowel marks",
    "Ibn Battuta: thirty years on the road",
    "Al-Jahiz and his Book of Animals",
    "Al-Khalil and the first Arabic dictionary",
    "Ibn Khaldun and why states rise and fall",
    "Cordoba: a city of libraries in Andalusia",
    "Ibn Sina, the doctor who wrote at night",
    "The Thousand and One Nights: where the stories came from",
    "Taha Hussein: the blind boy who became Egypt's minister of education",
    "Naguib Mahfouz and the cafes of Cairo",
    "Ghassan Kanafani and Men in the Sun",
]


def _state_path():
    return podcast_service.PODCAST_DIR / "walk-state.json"


def load_state() -> dict:
    state = {"firm_days": DEFAULT_FIRM_DAYS, "queue": [], "used_topics": [], "series": None, "last_script": None}
    path = _state_path()
    if path.exists():
        try:
            state.update(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return state


def save_state(state: dict) -> None:
    podcast_service.PODCAST_DIR.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _episodes(format_type: str) -> list[dict]:
    episodes = []
    for path in podcast_service.PODCAST_DIR.glob(f"{format_type}-*.mp3"):
        if path.name.startswith(SAMPLE_PREFIX):
            continue
        meta = podcast_service._load_metadata(path) or {}
        if meta.get("format_type") != format_type:
            continue
        episodes.append(dict(meta, filename=path.name, size_bytes=path.stat().st_size))
    episodes.sort(key=lambda m: m.get("generated_at") or "")
    return episodes


def walk_episodes() -> list[dict]:
    """Metadata of all walk episodes, oldest first, each with its ``filename``."""
    return _episodes("walk")


def feed_episodes() -> list[dict]:
    """Walk episodes and bridge episodes (authentic audio with a spoken bridge), oldest first."""
    return sorted(_episodes("walk") + _episodes("bridge"), key=lambda m: m.get("generated_at") or "")


def _step(firm_days: float, delta: int) -> int:
    nearest = min(range(len(FIRM_DAY_STEPS)), key=lambda i: abs(FIRM_DAY_STEPS[i] - firm_days))
    return FIRM_DAY_STEPS[max(0, min(len(FIRM_DAY_STEPS) - 1, nearest + delta))]


_DELTA = {"easy": -1, "right": 0, "hard": 1}


def record_feedback(filename: str, rating: str) -> dict:
    """Store the learner's rating of one episode and move the firm-word threshold.

    Changing an earlier rating applies only the difference, so tapping twice
    never moves the threshold twice.
    """
    from app.services.interaction_logger import log_interaction

    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS}")
    path = podcast_service._validate_filename(filename)
    if not path:
        raise LookupError(f"Podcast not found: {filename}")
    json_path = path.with_suffix(".json")
    meta = podcast_service._load_metadata(path) or {}
    previous = meta.get("feedback")
    meta["feedback"] = rating
    meta["feedback_at"] = datetime.now(timezone.utc).isoformat()
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    state = load_state()
    delta = _DELTA[rating] - _DELTA.get(previous, 0)
    if delta:
        state["firm_days"] = _step(state["firm_days"], delta)
        save_state(state)
    log_interaction(event="podcast_feedback", context=f"filename:{path.name}", feedback=rating, firm_days=state["firm_days"])
    return {"filename": path.name, "feedback": rating, "firm_days": state["firm_days"]}


def add_topic(title: str, notes: str = "") -> dict:
    title = " ".join(title.split())
    if not title:
        raise ValueError("title is empty")
    state = load_state()
    state["queue"].append({"title": title[:200], "notes": notes.strip()[:2000]})
    save_state(state)
    return state


def remove_topic(index: int) -> dict:
    state = load_state()
    if not 0 <= index < len(state["queue"]):
        raise LookupError("no such topic")
    state["queue"].pop(index)
    save_state(state)
    return state


def next_topic(state: dict) -> dict:
    """Take the learner's next queued topic, or the next unused built-in one."""
    if state["queue"]:
        topic = state["queue"].pop(0)
    else:
        unused = [t for t in SEED_TOPICS if t not in state["used_topics"]]
        if not unused:
            state["used_topics"] = []
            unused = list(SEED_TOPICS)
        topic = {"title": unused[0], "notes": ""}
    state["used_topics"].append(topic["title"])
    return topic


def generation_decision(now: datetime | None = None) -> tuple[bool, str]:
    """Whether the daily job should make an episode now, with the reason."""
    now = now or datetime.now()
    episodes = walk_episodes()
    today = now.strftime("%Y-%m-%d")
    if any((m.get("generated_at") or "")[:10] == today for m in episodes):
        return False, "an episode was already made today"
    unacknowledged = 0
    for meta in reversed(episodes):
        if meta.get("listened_at") or meta.get("feedback"):
            break
        unacknowledged += 1
    if unacknowledged >= MAX_UNACKNOWLEDGED:
        return False, f"{unacknowledged} episodes in a row have no completion or feedback"
    return True, "ok"


def status() -> dict:
    state = load_state()
    ok, reason = generation_decision()
    series = state.get("series") or {}
    return {
        "firm_days": state["firm_days"],
        "queue": state["queue"],
        "current_topic": series.get("title"),
        "will_generate": ok,
        "reason": reason,
    }


def build_feed(base_url: str) -> str:
    """RSS feed of the walk and bridge episodes, newest first. ``base_url`` is the private API prefix."""
    base = base_url.rstrip("/")
    items = []
    for meta in reversed(feed_episodes()):
        try:
            published = datetime.fromisoformat(meta.get("generated_at") or "")
        except ValueError:
            published = datetime.now()
        if published.tzinfo is None:
            published = published.astimezone()
        words = "، ".join(w.get("arabic", "") for w in meta.get("key_words", [])[:12])
        description = " ".join(part for part in (meta.get("summary", ""), words) if part)
        url = f"{base}/api/podcasts/audio/{meta['filename']}"
        items.append(
            "<item>"
            f"<title>{escape(meta.get('title_en') or meta['filename'])}</title>"
            f"<description>{escape(description)}</description>"
            f"<guid isPermaLink=\"false\">{escape(meta['filename'])}</guid>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f"<enclosure url=\"{escape(url, {chr(34): '&quot;'})}\" length=\"{meta['size_bytes']}\" type=\"audio/mpeg\"/>"
            f"<itunes:duration>{int(meta.get('duration_seconds') or 0)}</itunes:duration>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Alif Walk</title>"
        f"<link>{escape(base)}</link>"
        "<description>Arabic conversations for a walk</description>"
        "<language>ar</language><itunes:block>Yes</itunes:block><itunes:explicit>false</itunes:explicit>"
        + "".join(items)
        + "</channel></rss>"
    )
