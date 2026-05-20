"""Transparent reverse proxy for the polyglot backend.

**Why this exists**: lets the iOS / web client talk to ONE host:port
(``alif:3000``) instead of two, so EAS dev-client binaries, ATS exceptions,
and firewall whitelists don't need to know about polyglot's port. The
client calls ``alif:3000/polyglot/api/...`` and this module forwards
byte-for-byte to ``127.0.0.1:3002/api/...``.

**What this module MUST NOT do**:

- Import from polyglot. Anything.
- Introspect request or response payloads.
- Apply business logic, validation, auth, or schema awareness.
- Cache, retry, or transform anything beyond hop-by-hop headers.

The whole point is that polyglot stays independently testable + deployable
while sharing alif's externally-visible port. If you find yourself reaching
for any of the above, you're either (a) building a feature that belongs in
polyglot itself, or (b) it's time to swap this module for real nginx /
Caddy.

When real nginx arrives, delete this file. It will not be missed.
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/polyglot", tags=["polyglot-proxy"])

POLYGLOT_UPSTREAM = os.environ.get("POLYGLOT_UPSTREAM_URL", "http://127.0.0.1:3002")

# Long timeout because polyglot's page-view endpoint can spend 2-3 minutes
# inside the LLM quality gate on first view of an unwarmed page. Anything
# shorter and we'd surface spurious 504s during normal reading.
_PROXY_TIMEOUT_S = float(os.environ.get("POLYGLOT_PROXY_TIMEOUT_S", "300"))

# Hop-by-hop headers per RFC 7230 §6.1 — must not be forwarded.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    # `host` would point at the alif domain — let httpx set the right one.
    "host",
    # `content-length` is recomputed by the upstream client; passing it
    # through can mismatch and break streaming.
    "content-length",
}

# Single shared async client. FastAPI's lifespan would be the strict place
# to manage this, but a module-level client is simpler and httpx handles
# pooling internally. The connect timeout is short (5s) because polyglot
# is on localhost — if it's not up, fail fast.
#
# trust_env=False because polyglot lives on 127.0.0.1; any HTTP_PROXY /
# HTTPS_PROXY / ALL_PROXY env var in the shell that started uvicorn would
# otherwise route loopback traffic through the proxy and break it (also
# triggers a `socksio not installed` ImportError under tests).
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(_PROXY_TIMEOUT_S, connect=5.0),
    trust_env=False,
)


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(request: Request, path: str) -> Response:
    upstream_url = f"{POLYGLOT_UPSTREAM}/{path}"
    forwarded_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    body = await request.body()

    try:
        upstream = await _client.request(
            request.method,
            upstream_url,
            params=request.query_params,
            headers=forwarded_headers,
            content=body,
        )
    except httpx.ConnectError:
        return Response(
            status_code=502,
            content=b'{"detail":"polyglot upstream unavailable"}',
            media_type="application/json",
        )
    except httpx.ReadTimeout:
        return Response(
            status_code=504,
            content=b'{"detail":"polyglot upstream timed out"}',
            media_type="application/json",
        )

    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
        # Drop content-encoding because httpx already decoded the body —
        # passing the header through would make the client try to decode
        # plain bytes as gzip.
        and k.lower() != "content-encoding"
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
