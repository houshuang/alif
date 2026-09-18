"""The fast suite's provider guard is active and fails closed (see provider_guard.py)."""

import os
import socket
import subprocess

import litellm
import pytest

from app.config import settings
from app.services import background_threads, codex_cli
from app.services.llm import AllProvidersFailed, generate_completion
from tests.provider_guard import CREDENTIAL_ENV_VARS, SETTINGS_CREDENTIAL_FIELDS


def _attempts_since(guard, start):
    return guard.attempts[start:]


def test_credentials_are_blank_in_env_and_settings():
    for name in CREDENTIAL_ENV_VARS:
        assert os.environ.get(name) == ""
    for field in SETTINGS_CREDENTIAL_FIELDS:
        assert getattr(settings, field) == ""


def test_generate_completion_fails_closed_with_production_error(provider_guard):
    start = len(provider_guard.attempts)

    with pytest.raises(AllProvidersFailed):
        generate_completion("hello", task_type="guard_probe")
    with pytest.raises(AllProvidersFailed):
        generate_completion("hello", model_override="claude_sonnet", cli_only=True)

    targets = {row["target"] for row in _attempts_since(provider_guard, start)}
    assert "codex_cli" in targets
    assert "litellm" not in targets  # no credentials, so the API chain is never reached


def test_runner_stubs_raise_their_own_failure_types(provider_guard):
    start = len(provider_guard.attempts)

    with pytest.raises(codex_cli.CodexCLIError):
        codex_cli.generate_via_codex_cli(prompt="hello")
    with pytest.raises(litellm.APIConnectionError):
        litellm.completion(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])

    rows = _attempts_since(provider_guard, start)
    assert [row["target"] for row in rows] == ["codex_cli", "litellm"]
    assert all(row["test"].endswith("test_runner_stubs_raise_their_own_failure_types") for row in rows)


def test_external_network_is_refused(provider_guard):
    with provider_guard.expect_backstop() as hits:
        with pytest.raises(OSError):
            socket.create_connection(("api.openai.com", 443), timeout=1)
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("generativelanguage.googleapis.com", 443)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw:
            with pytest.raises(ConnectionRefusedError):
                raw.connect(("93.184.216.34", 80))

    assert {hit["target"] for hit in hits} == {
        "api.openai.com", "generativelanguage.googleapis.com", "93.184.216.34",
    }
    assert provider_guard.backstop == []


def test_loopback_is_not_blocked(provider_guard):
    with provider_guard.expect_backstop() as hits:
        assert socket.getaddrinfo("localhost", 80)
        for host in ("127.0.0.1", "localhost"):
            try:
                socket.create_connection((host, 9), timeout=1).close()
            except OSError:
                pass  # nothing listens on the discard port; only the OS may refuse

    assert hits == []


def test_provider_cli_spawn_is_refused(provider_guard):
    with provider_guard.expect_backstop() as hits:
        for exe in ("claude", "codex"):
            with pytest.raises(FileNotFoundError):
                subprocess.run([exe, "--version"], capture_output=True, timeout=5)

    assert [hit["target"] for hit in hits] == ["claude", "codex"]


def test_background_provider_threads_are_disabled():
    assert background_threads.start_daemon(lambda: None) is False


def test_real_providers_restores_then_reblanks(provider_guard):
    with provider_guard.real_providers():
        assert provider_guard.allowed
        for field in SETTINGS_CREDENTIAL_FIELDS:
            assert getattr(settings, field) == provider_guard._saved_settings[field]

    assert not provider_guard.allowed
    assert all(os.environ[name] == "" for name in CREDENTIAL_ENV_VARS)
    assert all(getattr(settings, field) == "" for field in SETTINGS_CREDENTIAL_FIELDS)
