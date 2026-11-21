import json
from datetime import datetime

import pytest

from app.services import podcast_service, walk_episodes


@pytest.fixture
def podcast_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast_service, "PODCAST_DIR", tmp_path)
    return tmp_path


def make_episode(directory, name, generated_at, **meta):
    (directory / f"{name}.mp3").write_bytes(b"x" * 10)
    data = {"format_type": "walk", "title_en": name, "generated_at": generated_at, "duration_seconds": 60}
    data.update(meta)
    (directory / f"{name}.json").write_text(json.dumps(data))


def test_feedback_moves_threshold_once_per_episode(podcast_dir):
    make_episode(podcast_dir, "walk-a-1", "2026-11-20T04:00:00")
    assert walk_episodes.record_feedback("walk-a-1.mp3", "hard")["firm_days"] == 60
    assert walk_episodes.record_feedback("walk-a-1.mp3", "hard")["firm_days"] == 60
    assert walk_episodes.record_feedback("walk-a-1.mp3", "right")["firm_days"] == 30
    assert walk_episodes.record_feedback("walk-a-1.mp3", "easy")["firm_days"] == 14
    assert json.loads((podcast_dir / "walk-a-1.json").read_text())["feedback"] == "easy"


def test_feedback_rejects_unknown_rating_and_file(podcast_dir):
    make_episode(podcast_dir, "walk-a-1", "2026-11-20T04:00:00")
    with pytest.raises(ValueError):
        walk_episodes.record_feedback("walk-a-1.mp3", "great")
    with pytest.raises(LookupError):
        walk_episodes.record_feedback("missing.mp3", "right")


def test_threshold_stays_inside_steps(podcast_dir):
    for i in range(8):
        make_episode(podcast_dir, f"walk-a-{i}", f"2026-11-1{i}T04:00:00")
        walk_episodes.record_feedback(f"walk-a-{i}.mp3", "hard")
    assert walk_episodes.load_state()["firm_days"] == walk_episodes.FIRM_DAY_STEPS[-1]


def test_daily_job_stops_after_unacknowledged_episodes(podcast_dir):
    now = datetime(2026, 11, 25, 4, 0)
    for day in range(20, 24):
        make_episode(podcast_dir, f"walk-a-{day}", f"2026-11-{day}T04:00:00")
    assert walk_episodes.generation_decision(now)[0] is False
    walk_episodes.record_feedback("walk-a-23.mp3", "right")
    assert walk_episodes.generation_decision(now)[0] is True


def test_daily_job_makes_one_episode_per_day(podcast_dir):
    make_episode(podcast_dir, "walk-a-1", "2026-11-25T03:00:00", feedback="right")
    assert walk_episodes.generation_decision(datetime(2026, 11, 25, 4, 0))[0] is False
    assert walk_episodes.generation_decision(datetime(2026, 11, 26, 4, 0))[0] is True


def test_samples_and_other_formats_are_not_walk_episodes(podcast_dir):
    make_episode(podcast_dir, "walk-sample-x", "2026-11-20T04:00:00")
    make_episode(podcast_dir, "walk-old", "2026-11-20T04:00:00", format_type="story")
    make_episode(podcast_dir, "walk-a-1", "2026-11-21T04:00:00")
    assert [m["filename"] for m in walk_episodes.walk_episodes()] == ["walk-a-1.mp3"]


def test_topic_queue_comes_before_built_in_topics(podcast_dir):
    walk_episodes.add_topic("  The   history of paper ", "from China")
    state = walk_episodes.load_state()
    assert walk_episodes.next_topic(state) == {"title": "The history of paper", "notes": "from China"}
    assert walk_episodes.next_topic(state)["title"] == walk_episodes.SEED_TOPICS[0]
    assert walk_episodes.next_topic(state)["title"] == walk_episodes.SEED_TOPICS[1]
    with pytest.raises(ValueError):
        walk_episodes.add_topic("   ")


def test_feed_lists_episodes_newest_first_with_private_urls(podcast_dir):
    make_episode(podcast_dir, "walk-a-1", "2026-11-20T04:00:00", title_en="One & two")
    make_episode(podcast_dir, "walk-a-2", "2026-11-21T04:00:00")
    feed = walk_episodes.build_feed("https://example.org/secret/")
    assert feed.index("walk-a-2.mp3") < feed.index("walk-a-1.mp3")
    assert 'url="https://example.org/secret/api/podcasts/audio/walk-a-1.mp3"' in feed
    assert "One &amp; two" in feed
    import xml.etree.ElementTree as ET

    assert len(ET.fromstring(feed).findall("./channel/item")) == 2


def test_feed_route_requires_https_base_on_same_host(podcast_dir, client):
    make_episode(podcast_dir, "walk-a-1", "2026-11-20T04:00:00")
    ok = client.get("/api/podcasts/feed.xml", params={"base": "https://testserver/cap"})
    assert ok.status_code == 200 and "walk-a-1.mp3" in ok.text
    assert client.get("/api/podcasts/feed.xml", params={"base": "http://testserver/cap"}).status_code == 400
    assert client.get("/api/podcasts/feed.xml", params={"base": "https://elsewhere.org/cap"}).status_code == 400


def test_feedback_and_topic_routes(podcast_dir, client):
    make_episode(podcast_dir, "walk-a-1", "2026-11-20T04:00:00")
    assert client.post("/api/podcasts/feedback/walk-a-1.mp3", json={"rating": "hard"}).json()["firm_days"] == 60
    assert client.post("/api/podcasts/feedback/walk-a-1.mp3", json={"rating": "nope"}).status_code == 400
    listed = client.get("/api/podcasts").json()["podcasts"]
    assert listed[0]["feedback"] == "hard"
    status = client.post("/api/podcasts/walk/topics", json={"title": "Ibn Rushd"}).json()
    assert status["queue"] == [{"title": "Ibn Rushd", "notes": ""}]
    assert client.delete("/api/podcasts/walk/topics/0").json()["queue"] == []
    assert client.delete("/api/podcasts/walk/topics/5").status_code == 404
