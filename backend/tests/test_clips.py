import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import ClipProgress
from app.routers import clips as clips_router

CLIP_ID = "7702463106848525586"


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    (tmp_path / "video").mkdir()
    (tmp_path / "clips.json").write_text(
        json.dumps({"clips": [{"id": CLIP_ID, "title": "t", "lines": []}], "core": [["ده", "da", "this"]]}),
        encoding="utf-8",
    )
    (tmp_path / "video" / f"{CLIP_ID}.mp4").write_bytes(b"0123456789")
    monkeypatch.setattr(clips_router, "CLIPS_DIR", tmp_path)
    return tmp_path


def test_empty_when_catalogue_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(clips_router, "CLIPS_DIR", tmp_path / "absent")
    assert client.get("/api/clips").json() == {"clips": [], "core": []}


def test_list_has_default_progress_and_core(client, catalogue):
    body = client.get("/api/clips").json()
    assert body["core"] == [["ده", "da", "this"]]
    assert body["clips"][0]["progress"] == {
        "stage": 0, "pass_count": 0, "retest_due": False, "next_check_at": None,
    }


def test_video_supports_range_requests(client, catalogue):
    res = client.get(f"/api/clips/{CLIP_ID}/video", headers={"Range": "bytes=0-3"})
    assert res.status_code == 206
    assert res.content == b"0123"
    assert res.headers["content-type"] == "video/mp4"


@pytest.mark.parametrize("clip_id", ["999", "..%2Fclips", "abc"])
def test_unknown_or_invalid_clip_is_404(client, catalogue, clip_id):
    assert client.get(f"/api/clips/{clip_id}/video").status_code == 404


def test_stage_records_the_current_step(client, catalogue):
    url = f"/api/clips/{CLIP_ID}/event"
    assert client.post(url, json={"event": "stage", "stage": 3}).json()["stage"] == 3
    assert client.post(url, json={"event": "stage", "stage": 1}).json()["stage"] == 1


def test_pass_walks_the_retest_ladder(client, catalogue, db_session):
    url = f"/api/clips/{CLIP_ID}/event"
    gaps = []
    for _ in range(6):
        assert client.post(url, json={"event": "pass"}).status_code == 200
        db_session.expire_all()
        row = db_session.get(ClipProgress, CLIP_ID)
        gaps.append(round((row.next_check_at - row.last_passed_at).total_seconds() / 86400))
    assert gaps == [1, 3, 7, 21, 60, 60]
    assert row.pass_count == 6


def test_retest_due_after_next_check_passes(client, catalogue, db_session):
    client.post(f"/api/clips/{CLIP_ID}/event", json={"event": "pass"})
    db_session.expire_all()
    row = db_session.get(ClipProgress, CLIP_ID)
    row.next_check_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    db_session.commit()
    assert client.get("/api/clips").json()["clips"][0]["progress"]["retest_due"] is True


def test_event_requires_its_payload(client, catalogue):
    url = f"/api/clips/{CLIP_ID}/event"
    assert client.post(url, json={"event": "stage"}).status_code == 422
    assert client.post(url, json={"event": "blind_rating"}).status_code == 422
