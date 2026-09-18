"""Fail-closed LLM provider guard for the fast test suite.

The fast suite must never reach a real model: a developer shell exports
provider keys and has ``claude``/``codex`` on PATH, so an unguarded run spends
quota and blocks on HTTPS for half an hour. ``install()`` runs from
conftest.py before the app is imported and:

1. Blanks every provider credential, in the environment and in the app's
   settings (which would otherwise load backend/.env).
2. Replaces the runners that every provider call goes through (the Claude
   CLI in limbic, the Codex CLI and litellm) with stubs that raise the
   runner's own failure type, so callers take their real "provider failed"
   path. These attempts are expected and only recorded.
3. As a backstop, refuses to spawn ``claude``/``codex`` and refuses network
   connections to anything but loopback. A hit here means some provider path
   bypassed step 2; conftest fails the run and names the test.

Tests marked ``slow`` make real calls on purpose and run inside
``real_providers()``.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import socket
import subprocess
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path

CREDENTIAL_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_KEY",
    "GEMINI_API_KEY",
    "GEMINI_KEY",
    "GOOGLE_API_KEY",
    "ELEVENLABS_API_KEY",
    "SONIOX_API_KEY",
)
SETTINGS_CREDENTIAL_FIELDS = (
    "openai_key",
    "anthropic_api_key",
    "anthropic_key",
    "gemini_key",
    "elevenlabs_api_key",
    "soniox_api_key",
)
_LOOPBACK_NAMES = {"localhost", "testserver"}
_CLI_NAMES = {"claude", "codex"}
_APP_DIR = str(Path(__file__).resolve().parents[1] / "app")


def _current_test() -> str:
    raw = os.environ.get("PYTEST_CURRENT_TEST", "")
    return raw.rsplit(" (", 1)[0] if raw else "<no test running>"


def _app_frames() -> list[str]:
    frames = [
        f"{Path(fr.filename).relative_to(Path(_APP_DIR).parent)}:{fr.lineno}:{fr.name}"
        for fr in traceback.extract_stack()
        if fr.filename.startswith(_APP_DIR)
    ]
    return frames[-4:]


def _is_loopback(host) -> bool:
    if host is None or host == "":
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    host = str(host)
    if host in _LOOPBACK_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return addr.is_loopback or addr.is_unspecified


class ProviderGuard:
    def __init__(self) -> None:
        self.allowed = False
        self.attempts: list[dict] = []
        self.backstop: list[dict] = []
        self._lock = threading.Lock()
        self._saved_env: dict[str, str | None] = {}
        self._saved_settings: dict[str, str] = {}
        self._settings = None
        self._log_path = os.environ.get("ALIF_TEST_PROVIDER_LOG")

    def _record(self, bucket: list[dict], layer: str, target: str) -> dict:
        row = {
            "layer": layer,
            "target": target,
            "test": _current_test(),
            "thread": threading.current_thread().name,
            "app_frames": _app_frames(),
        }
        with self._lock:
            bucket.append(row)
            if self._log_path:
                with open(self._log_path, "a") as fh:
                    fh.write(json.dumps(row) + "\n")
        return row

    # ── credentials ────────────────────────────────────────────────────────

    def scrub_credentials(self, settings) -> None:
        self._settings = settings
        for name in CREDENTIAL_ENV_VARS:
            self._saved_env[name] = os.environ.get(name)
            os.environ[name] = ""
        for field in SETTINGS_CREDENTIAL_FIELDS:
            self._saved_settings[field] = getattr(settings, field, "")
            setattr(settings, field, "")

    @contextmanager
    def real_providers(self):
        """Lift the guard and restore credentials (for tests marked slow)."""
        self.allowed = True
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for field, value in self._saved_settings.items():
            setattr(self._settings, field, value)
        try:
            yield
        finally:
            for name in self._saved_env:
                os.environ[name] = ""
            for field in self._saved_settings:
                setattr(self._settings, field, "")
            self.allowed = False

    # ── tier 1: provider runners ───────────────────────────────────────────

    def _runner_stub(self, original, target: str, make_error, *, is_async: bool = False):
        guard = self

        if is_async:
            async def stub(*args, **kwargs):
                if guard.allowed:
                    return await original(*args, **kwargs)
                guard._record(guard.attempts, "runner", target)
                raise make_error(args, kwargs)
        else:
            def stub(*args, **kwargs):
                if guard.allowed:
                    return original(*args, **kwargs)
                guard._record(guard.attempts, "runner", target)
                raise make_error(args, kwargs)

        stub.__wrapped__ = original
        stub.__name__ = getattr(original, "__name__", target)
        return stub

    def guard_runners(self) -> None:
        import litellm
        import limbic.cerebellum.claude_cli as claude_cli

        message = "LLM provider call blocked by the fast-suite provider guard"
        # Patch limbic before anything imports app.services.claude_code, which
        # binds limbic's generate at import time.
        claude_cli.generate = self._runner_stub(
            claude_cli.generate, "claude_cli",
            lambda a, kw: claude_cli.ClaudeCLIError(message),
        )
        from app.services import codex_cli

        codex_cli.generate_via_codex_cli = self._runner_stub(
            codex_cli.generate_via_codex_cli, "codex_cli",
            lambda a, kw: codex_cli.CodexCLIError(message),
        )

        def litellm_error(args, kwargs):
            return litellm.APIConnectionError(
                message=message, llm_provider="test-guard", model=str(kwargs.get("model", "")),
            )

        litellm.completion = self._runner_stub(litellm.completion, "litellm", litellm_error)
        litellm.acompletion = self._runner_stub(
            litellm.acompletion, "litellm", litellm_error, is_async=True,
        )

    # ── tier 2: process and network backstop ───────────────────────────────

    def _refuse(self, layer: str, target: str) -> str:
        row = self._record(self.backstop, layer, target)
        return f"[provider guard] {row['test']} tried to reach {target} ({layer})"

    def install_backstop(self) -> None:
        guard = self
        popen_init = subprocess.Popen.__init__

        def guarded_popen_init(self, args, *a, **kw):
            argv = [str(x) for x in args] if isinstance(args, (list, tuple)) else [str(args)]
            exe = os.path.basename(argv[0]) if argv else ""
            is_cli = exe in _CLI_NAMES or (exe == "node" and any("codex" in x for x in argv[1:3]))
            if is_cli and not guard.allowed:
                raise FileNotFoundError(errno.ENOENT, guard._refuse("spawn", exe), exe)
            return popen_init(self, args, *a, **kw)

        subprocess.Popen.__init__ = guarded_popen_init

        getaddrinfo = socket.getaddrinfo

        def guarded_getaddrinfo(host, *a, **kw):
            if not guard.allowed and not _is_loopback(host):
                raise socket.gaierror(socket.EAI_NONAME, guard._refuse("dns", str(host)))
            return getaddrinfo(host, *a, **kw)

        socket.getaddrinfo = guarded_getaddrinfo

        create_connection = socket.create_connection

        def guarded_create_connection(address, *a, **kw):
            host = address[0] if isinstance(address, tuple) else address
            if not guard.allowed and not _is_loopback(host):
                raise ConnectionRefusedError(errno.ECONNREFUSED, guard._refuse("connect", str(host)))
            return create_connection(address, *a, **kw)

        socket.create_connection = guarded_create_connection

        connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

        def _blocked(sock, address) -> str | None:
            if guard.allowed or sock.family not in (socket.AF_INET, socket.AF_INET6):
                return None
            host = address[0] if isinstance(address, tuple) else address
            return None if _is_loopback(host) else str(host)

        def guarded_connect(sock, address):
            host = _blocked(sock, address)
            if host is not None:
                raise ConnectionRefusedError(errno.ECONNREFUSED, guard._refuse("connect", host))
            return connect(sock, address)

        def guarded_connect_ex(sock, address):
            host = _blocked(sock, address)
            if host is not None:
                guard._refuse("connect", host)
                return errno.ECONNREFUSED
            return connect_ex(sock, address)

        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex

    @contextmanager
    def expect_backstop(self):
        """Yield the backstop hits made inside the block and forget them.

        Only for tests that deliberately probe the backstop.
        """
        start = len(self.backstop)
        hits: list[dict] = []
        try:
            yield hits
        finally:
            with self._lock:
                hits.extend(self.backstop[start:])
                del self.backstop[start:]


GUARD = ProviderGuard()


def install(settings) -> ProviderGuard:
    """Scrub credentials and install both tiers. Call before importing app.main."""
    GUARD.scrub_credentials(settings)
    GUARD.guard_runners()
    GUARD.install_backstop()
    return GUARD
