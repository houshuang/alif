"""Tests for the Mac-side backup transport and publication fences.

The Koigen repository owns exhaustive bundle/schema/restore tests.  These tests use
a small verifier double so this repository can independently prove that backup.sh
stages, verifies, atomically publishes, and rejects corrupt or stale transfers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup.sh"


class BackupScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.local = self.base / "local"
        self.local.mkdir(mode=0o700)
        self.koigen_local = self.local / "koigen"
        self.koigen_local.mkdir(mode=0o700)
        self.remote = self.base / "remote"
        self.remote.mkdir(mode=0o700)
        self.tool_dir = self.base / "tool"
        self.tool_dir.mkdir()
        self.tool = self.tool_dir / "durable_backup.py"
        self._write_verifier_double()
        self._write_transport_doubles()

        self.env = os.environ.copy()
        self.env.update(
            {
                "ALIF_BACKUP_SOURCE_ONLY": "1",
                "ALIF_BACKUP_PATH": f"{self.bin}:{os.environ['PATH']}",
                "ALIF_BACKUP_DIR": str(self.local),
                "KOIGEN_LOCAL_BACKUP_DIR": str(self.koigen_local),
                "KOIGEN_VERIFY_TOOL": str(self.tool),
                "FAKE_REMOTE_DIR": str(self.remote),
            }
        )

    @staticmethod
    def _write_executable(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _write_verifier_double(self) -> None:
        self.tool.write_text(
            textwrap.dedent(
                """
                import datetime as dt
                import hashlib
                import json
                from pathlib import Path

                def verify_snapshot(snapshot):
                    snapshot = Path(snapshot)
                    manifest = json.loads((snapshot / "manifest.json").read_text())
                    payload = snapshot / "state.tar.gz"
                    assert manifest["snapshot_id"] == snapshot.name
                    assert hashlib.sha256(payload.read_bytes()).hexdigest() == manifest["sha256"]
                    return {
                        "snapshot_id": manifest["snapshot_id"],
                        "created_at": manifest["created_at"],
                        "files": 1,
                        "bytes": payload.stat().st_size,
                    }

                def prune_snapshots(destination, *, retention_days, minimum_snapshots, keep):
                    # Bundle retention itself is covered by Koigen's test suite. The
                    # shell contract only needs a callable, successful policy owner.
                    return []
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def _write_transport_doubles(self) -> None:
        self._write_executable(
            self.bin / "ssh",
            textwrap.dedent(
                """
                #!/bin/bash
                set -eu
                if [[ "$*" == *".backup"* ]]; then
                    cat "$FAKE_ALIF_DB"
                else
                    printf '%s\n' "$FAKE_SNAPSHOT_NAME"
                fi
                """
            ).lstrip(),
        )
        self._write_executable(
            self.bin / "rsync",
            textwrap.dedent(
                """
                #!/bin/bash
                set -eu
                while [ "$#" -gt 2 ]; do shift; done
                source=${1%/}
                destination=$2
                name=${source##*/}
                cp -R "$FAKE_REMOTE_DIR/$name/." "$destination/"
                if [ "${FAKE_CORRUPT_TRANSFER:-0}" = "1" ]; then
                    printf 'corruption' >>"$destination/state.tar.gz"
                fi
                """
            ).lstrip(),
        )

    def _run_function(self, function: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = f"source {SCRIPT!s}; {function}"
        return subprocess.run(
            ["/bin/bash", "-c", command],
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def _make_remote_snapshot(self, created: dt.datetime | None = None) -> str:
        created = created or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        payload = b"private durable fixture"
        digest = hashlib.sha256(payload).hexdigest()
        name = f"koigen-durable-v1-{created:%Y%m%dT%H%M%SZ}-{digest[:12]}"
        snapshot = self.remote / name
        snapshot.mkdir(mode=0o700)
        (snapshot / "manifest.json").write_text(
            json.dumps(
                {
                    "snapshot_id": name,
                    "created_at": created.isoformat().replace("+00:00", "Z"),
                    "sha256": digest,
                }
            ),
            encoding="utf-8",
        )
        (snapshot / "state.tar.gz").write_bytes(payload)
        for path in snapshot.iterdir():
            path.chmod(0o600)
        self.env["FAKE_SNAPSHOT_NAME"] = name
        return name

    def test_koigen_pull_verifies_before_atomic_publication_and_is_idempotent(self) -> None:
        name = self._make_remote_snapshot()

        first = self._run_function("pull_koigen_snapshot")
        self.assertIn("Koigen backup saved", first.stdout)
        final = self.koigen_local / name
        self.assertTrue(final.is_dir())
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o700)
        self.assertEqual(list(self.koigen_local.glob(".incoming-*")), [])

        second = self._run_function("pull_koigen_snapshot")
        self.assertIn("Koigen backup already current", second.stdout)

    def test_corrupt_koigen_transfer_never_gets_final_name(self) -> None:
        name = self._make_remote_snapshot()
        self.env["FAKE_CORRUPT_TRANSFER"] = "1"

        result = self._run_function("pull_koigen_snapshot", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.koigen_local / name).exists())
        self.assertEqual(list(self.koigen_local.glob(".incoming-*")), [])

    def test_stale_koigen_transfer_never_gets_final_name(self) -> None:
        name = self._make_remote_snapshot(
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=49)
        )

        result = self._run_function("pull_koigen_snapshot", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.koigen_local / name).exists())

    def test_alif_database_is_integrity_checked_before_final_name(self) -> None:
        database = self.base / "source.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('ok')")
        connection.commit()
        connection.close()
        self.env["FAKE_ALIF_DB"] = str(database)
        self.env["ALIF_BACKUP_TIMESTAMP"] = "20260710_090000"

        result = self._run_function("snapshot_alif_database")

        final = self.local / "alif_20260710_090000.db"
        self.assertIn("Alif backup saved", result.stdout)
        self.assertTrue(final.is_file())
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o600)
        with sqlite3.connect(final) as restored:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))

    def test_corrupt_alif_stream_never_gets_final_name(self) -> None:
        corrupt = self.base / "corrupt.db"
        corrupt.write_bytes(b"not sqlite")
        self.env["FAKE_ALIF_DB"] = str(corrupt)
        self.env["ALIF_BACKUP_TIMESTAMP"] = "20260710_090001"

        result = self._run_function("snapshot_alif_database", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.local / "alif_20260710_090001.db").exists())
        self.assertEqual(list(self.local.glob(".alif_*.tmp")), [])

    def test_alif_retention_keeps_daily_weekly_and_monthly_generations(self) -> None:
        today = dt.datetime.now().date()
        weekly = next(
            today - dt.timedelta(days=days)
            for days in range(8, 31)
            if (today - dt.timedelta(days=days)).weekday() == 6
        )
        discard_week = next(
            today - dt.timedelta(days=days)
            for days in range(8, 31)
            if (today - dt.timedelta(days=days)).weekday() != 6
        )
        monthly = (today - dt.timedelta(days=62)).replace(day=1)
        discard_month = monthly.replace(day=2)
        generations = {
            today - dt.timedelta(days=2): True,
            weekly: True,
            discard_week: False,
            monthly: True,
            discard_month: False,
        }
        paths: dict[dt.date, Path] = {}
        for date, _keep in generations.items():
            path = self.local / f"alif_{date:%Y%m%d}_090000.db"
            path.write_bytes(b"fixture")
            paths[date] = path

        result = self._run_function("prune_alif_backups")

        self.assertIn("Alif retention pruned: 2", result.stdout)
        for date, keep in generations.items():
            self.assertEqual(paths[date].exists(), keep, date.isoformat())

    def test_launchd_entrypoint_completes_with_verified_authorities(self) -> None:
        database = self.base / "source-main.db"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE sample(value TEXT)")
        self.env["FAKE_ALIF_DB"] = str(database)
        self.env["ALIF_BACKUP_TIMESTAMP"] = "20260710_090002"
        name = self._make_remote_snapshot()

        result = self._run_function("main")

        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.local / "alif_20260710_090002.db").is_file())
        self.assertTrue((self.koigen_local / name).is_dir())


if __name__ == "__main__":
    unittest.main()
