"""Throwaway pytest plugin: record (and block) every real LLM/network call.

Load with: PYTHONPATH=<this dir> pytest -p llm_leak_recorder
Writes one JSON line per intercepted call to $LLM_LEAK_LOG.
"""
import json
import os
import socket
import subprocess
import threading
import traceback

LOG = os.environ.get("LLM_LEAK_LOG", "/tmp/llm_leaks.jsonl")
_LOCK = threading.Lock()
_ALLOWED_HOSTS = {"testserver", "localhost", "127.0.0.1", "::1"}


def _app_frames():
    frames = []
    for fr in traceback.extract_stack()[:-2]:
        if "/backend/app/" in fr.filename or "/backend/scripts/" in fr.filename:
            short = fr.filename.split("/backend/", 1)[1]
            frames.append(f"{short}:{fr.lineno}:{fr.name}")
    return frames[-4:]


def _test_frames():
    for fr in reversed(traceback.extract_stack()):
        if "/backend/tests/" in fr.filename:
            return f"{fr.filename.split('/backend/', 1)[1]}:{fr.lineno}:{fr.name}"
    return None


def _record(kind, target):
    row = {
        "kind": kind,
        "target": target,
        "test": os.environ.get("PYTEST_CURRENT_TEST", "?"),
        "thread": threading.current_thread().name,
        "test_frame": _test_frames(),
        "app_frames": _app_frames(),
    }
    with _LOCK, open(LOG, "a") as fh:
        fh.write(json.dumps(row) + "\n")


_orig_popen_init = subprocess.Popen.__init__


def _popen_init(self, args, *a, **kw):
    argv = args if isinstance(args, (list, tuple)) else [str(args)]
    exe = os.path.basename(str(argv[0])) if argv else ""
    if exe in {"claude", "codex"} or (exe == "node" and any("codex" in str(x) for x in argv[:2])):
        _record("cli", exe)
        raise FileNotFoundError(f"[llm-leak-recorder] blocked {exe}")
    return _orig_popen_init(self, args, *a, **kw)


subprocess.Popen.__init__ = _popen_init

_orig_create_connection = socket.create_connection


def _create_connection(address, *a, **kw):
    host = address[0] if isinstance(address, tuple) else str(address)
    if host not in _ALLOWED_HOSTS:
        _record("network", host)
        raise OSError(f"[llm-leak-recorder] blocked network to {host}")
    return _orig_create_connection(address, *a, **kw)


socket.create_connection = _create_connection

_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host, *a, **kw):
    if host and str(host) not in _ALLOWED_HOSTS:
        _record("dns", str(host))
        raise socket.gaierror(f"[llm-leak-recorder] blocked DNS for {host}")
    return _orig_getaddrinfo(host, *a, **kw)


socket.getaddrinfo = _getaddrinfo
