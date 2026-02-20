"""Tests for tashkeel (diacritics) fading settings."""

from app.models import LearnerSettings
from app.services.topic_service import get_settings


def test_tashkeel_settings_default(db_session):
    """Default tashkeel mode is 'always'."""
    settings = get_settings(db_session)
    assert settings.tashkeel_mode in (None, "always")
    assert settings.tashkeel_stability_threshold in (None, 30.0)


def test_tashkeel_settings_update(db_session):
    """Can update tashkeel mode and threshold."""
    settings = get_settings(db_session)
    settings.tashkeel_mode = "fade"
    settings.tashkeel_stability_threshold = 15.0
    db_session.commit()

    refreshed = get_settings(db_session)
    assert refreshed.tashkeel_mode == "fade"
    assert refreshed.tashkeel_stability_threshold == 15.0


def test_tashkeel_api_get(client):
    """GET /api/settings/tashkeel returns current settings."""
    resp = client.get("/api/settings/tashkeel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] in ("always", "fade", "never")
    assert isinstance(data["stability_threshold"], (int, float))


def test_tashkeel_api_put(client):
    """PUT /api/settings/tashkeel updates settings."""
    resp = client.put("/api/settings/tashkeel", json={
        "mode": "fade",
        "stability_threshold": 20.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "fade"
    assert data["stability_threshold"] == 20.0

    # Verify persisted
    resp2 = client.get("/api/settings/tashkeel")
    assert resp2.json()["mode"] == "fade"


def test_tashkeel_api_invalid_mode(client):
    """Invalid mode returns 400."""
    resp = client.put("/api/settings/tashkeel", json={
        "mode": "invalid",
        "stability_threshold": 30.0,
    })
    assert resp.status_code == 400


def test_tashkeel_api_threshold_bounds(client):
    """Out-of-range threshold returns 400."""
    resp = client.put("/api/settings/tashkeel", json={
        "mode": "fade",
        "stability_threshold": 0.5,  # below 1.0 minimum
    })
    assert resp.status_code == 400
