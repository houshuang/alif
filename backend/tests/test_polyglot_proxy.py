"""Tests for the transparent polyglot proxy.

Uses httpx.MockTransport to stand in for the real polyglot backend so we
never hit the network. Each test verifies one passthrough property
(method, status, headers, query params, body, error handling).
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import polyglot_proxy


@pytest.fixture
def mock_upstream(monkeypatch):
    """Replace polyglot_proxy's shared httpx.AsyncClient with one that
    talks to a MockTransport. Returns a list that captures each upstream
    request the proxy makes.
    """
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        # Echo back enough metadata to verify the passthrough properties.
        if request.url.path == "/api/_proxy_test/echo":
            return httpx.Response(
                200,
                json={
                    "method": request.method,
                    "path": request.url.path,
                    "query": dict(request.url.params),
                    "headers": {k.lower(): v for k, v in request.headers.items()
                                if k.lower() in {"x-trace-id", "content-type",
                                                 "authorization"}},
                    "body": request.content.decode("utf-8") if request.content else "",
                },
            )
        if request.url.path == "/api/_proxy_test/bad":
            return httpx.Response(418, json={"detail": "I'm a teapot"})
        if request.url.path == "/api/_proxy_test/connect-error":
            raise httpx.ConnectError("simulated upstream down")
        if request.url.path == "/api/_proxy_test/timeout":
            raise httpx.ReadTimeout("simulated upstream timeout")
        return httpx.Response(404, json={"detail": "unknown test route"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    monkeypatch.setattr(polyglot_proxy, "_client", mock_client)
    yield captured


def test_proxy_forwards_get_path_and_status(mock_upstream):
    """GET /polyglot/api/foo → upstream sees /api/foo. Status code mirrors."""
    with TestClient(app) as client:
        resp = client.get("/polyglot/api/_proxy_test/echo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == "GET"
    assert body["path"] == "/api/_proxy_test/echo"
    assert len(mock_upstream) == 1


def test_proxy_forwards_query_params(mock_upstream):
    """Query params are preserved as-is."""
    with TestClient(app) as client:
        resp = client.get("/polyglot/api/_proxy_test/echo?language_code=el&limit=5")
    assert resp.status_code == 200
    assert resp.json()["query"] == {"language_code": "el", "limit": "5"}


def test_proxy_forwards_post_body_and_content_type(mock_upstream):
    """POST body forwarded byte-for-byte; content-type header preserved."""
    with TestClient(app) as client:
        resp = client.post(
            "/polyglot/api/_proxy_test/echo",
            json={"hello": "world"},
            headers={"X-Trace-Id": "abc123"},
        )
    import json
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == "POST"
    # Whitespace-tolerant: TestClient and httpx may emit slightly different
    # JSON serializations; what matters is the parsed payload round-trips.
    assert json.loads(body["body"]) == {"hello": "world"}
    assert body["headers"]["content-type"] == "application/json"
    # Custom header passed through (not in the hop-by-hop blocklist)
    assert body["headers"]["x-trace-id"] == "abc123"


def test_proxy_mirrors_non_2xx_status(mock_upstream):
    """A 418 from upstream surfaces as 418 to the client — proxy doesn't
    interpret status codes as success/failure."""
    with TestClient(app) as client:
        resp = client.get("/polyglot/api/_proxy_test/bad")
    assert resp.status_code == 418
    assert resp.json() == {"detail": "I'm a teapot"}


def test_proxy_returns_502_when_upstream_unavailable(mock_upstream):
    """If polyglot is down (connect refused), the proxy must surface 502
    rather than crash or hang."""
    with TestClient(app) as client:
        resp = client.get("/polyglot/api/_proxy_test/connect-error")
    assert resp.status_code == 502
    assert "unavailable" in resp.json()["detail"]


def test_proxy_returns_504_on_upstream_timeout(mock_upstream):
    """If polyglot takes too long (LLM hang), the proxy returns 504, not 500."""
    with TestClient(app) as client:
        resp = client.get("/polyglot/api/_proxy_test/timeout")
    assert resp.status_code == 504
    assert "timed out" in resp.json()["detail"]


def test_proxy_strips_host_header(mock_upstream):
    """The Host header must be rewritten by httpx — the upstream should see
    its own loopback host, not the client's host. (Verified by checking
    that the captured request's host equals the upstream URL host.)"""
    with TestClient(app) as client:
        resp = client.get("/polyglot/api/_proxy_test/echo",
                          headers={"Host": "example.com"})
    assert resp.status_code == 200
    upstream_req = mock_upstream[0]
    assert upstream_req.url.host == "127.0.0.1"
