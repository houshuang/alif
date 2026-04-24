"""Database setup for simulation — copy production DB to temp file."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def find_latest_backup(backup_dir: str | None = None) -> Path:
    """Find the most recent alif_*.db file in the backup directory."""
    backup_dir = Path(backup_dir or os.path.expanduser("~/alif-backups"))
    backups = sorted(backup_dir.glob("alif_*.db"), key=lambda p: p.stat().st_mtime)
    if not backups:
        raise FileNotFoundError(f"No backups found in {backup_dir}")
    return backups[-1]


def create_simulation_db(source_path: str | Path) -> tuple:
    """Copy source DB to temp file, return (engine, SessionFactory, tmp_path).

    The temp file is cleaned up on process exit.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Source DB not found: {source_path}")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="alif_sim_")
    os.close(tmp_fd)
    shutil.copy2(source_path, tmp_path)

    atexit.register(lambda: os.unlink(tmp_path) if os.path.exists(tmp_path) else None)

    engine = create_engine(
        f"sqlite:///{tmp_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    # Ensure all model columns exist (backup may predate schema changes)
    _apply_missing_columns(engine)

    SessionFactory = sessionmaker(bind=engine)
    return engine, SessionFactory, tmp_path


def _apply_missing_columns(engine):
    """Add any columns from the current model that are missing in the DB.

    This handles running simulations against older backups that predate
    recent migrations.
    """
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    with engine.connect() as conn:
        for table_name, expected_cols in [
            ("learner_settings", {
                "tashkeel_mode": "VARCHAR(10) DEFAULT 'always'",
                "tashkeel_stability_threshold": "FLOAT DEFAULT 30.0",
            }),
        ]:
            if table_name not in insp.get_table_names():
                continue
            existing = {col["name"] for col in insp.get_columns(table_name)}
            for col_name, col_def in expected_cols.items():
                if col_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"
                    ))
        conn.commit()
