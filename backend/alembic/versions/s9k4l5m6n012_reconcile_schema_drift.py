"""Reconcile drifted schema state on existing SQLite databases.

Revision ID: s9k4l5m6n012
Revises: r8j3k4l5m901
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa


revision = "s9k4l5m6n012"
down_revision = "r8j3k4l5m901"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _named_index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def _has_unique_single_column_key(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False

    for uq in inspector.get_unique_constraints(table_name):
        if (uq.get("column_names") or []) == [column_name]:
            return True

    for idx in inspector.get_indexes(table_name):
        if idx.get("unique") and (idx.get("column_names") or []) == [column_name]:
            return True

    # SQLite-specific fallback to detect autoindexes backing UNIQUE constraints.
    if bind.dialect.name == "sqlite":
        rows = bind.execute(sa.text(f"PRAGMA index_list('{table_name}')")).mappings().all()
        for row in rows:
            if int(row.get("unique", 0)) != 1:
                continue
            idx_name = row.get("name")
            if not idx_name:
                continue
            idx_cols = bind.execute(sa.text(f"PRAGMA index_info('{idx_name}')")).mappings().all()
            if [c.get("name") for c in idx_cols] == [column_name]:
                return True

    return False


def _dedupe_client_review_ids(bind, table_name: str) -> None:
    # Keep the earliest row per client_review_id, null out later duplicates.
    bind.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET client_review_id = NULL
            WHERE client_review_id IS NOT NULL
              AND id NOT IN (
                  SELECT MIN(id)
                  FROM {table_name}
                  WHERE client_review_id IS NOT NULL
                  GROUP BY client_review_id
              )
            """
        )
    )


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if _table_exists(bind, table_name) and not _column_exists(bind, table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    # Backfill known drifted columns.
    _add_column_if_missing("review_log", sa.Column("client_review_id", sa.String(50), nullable=True))
    _add_column_if_missing("review_log", sa.Column("is_acquisition", sa.Boolean(), server_default="0", nullable=True))
    _add_column_if_missing("sentence_review_log", sa.Column("client_review_id", sa.String(50), nullable=True))
    _add_column_if_missing("sentences", sa.Column("is_active", sa.Boolean(), server_default="1", nullable=True))
    _add_column_if_missing("sentences", sa.Column("created_at", sa.DateTime(), nullable=True))

    _add_column_if_missing("lemmas", sa.Column("cefr_level", sa.String(2), nullable=True))
    _add_column_if_missing("lemmas", sa.Column("thematic_domain", sa.String(30), nullable=True))
    _add_column_if_missing("lemmas", sa.Column("etymology_json", sa.JSON(), nullable=True))
    _add_column_if_missing("lemmas", sa.Column("memory_hooks_json", sa.JSON(), nullable=True))

    _add_column_if_missing("story_words", sa.Column("name_type", sa.String(20), nullable=True))

    _add_column_if_missing("user_lemma_knowledge", sa.Column("acquisition_box", sa.Integer(), nullable=True))
    _add_column_if_missing("user_lemma_knowledge", sa.Column("acquisition_next_due", sa.DateTime(), nullable=True))
    _add_column_if_missing("user_lemma_knowledge", sa.Column("acquisition_started_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("user_lemma_knowledge", sa.Column("graduated_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("user_lemma_knowledge", sa.Column("leech_suspended_at", sa.DateTime(), nullable=True))

    # Ensure acquisition due index exists.
    if (
        _table_exists(bind, "user_lemma_knowledge")
        and _column_exists(bind, "user_lemma_knowledge", "acquisition_box")
        and _column_exists(bind, "user_lemma_knowledge", "acquisition_next_due")
        and not _named_index_exists(bind, "user_lemma_knowledge", "ix_ulk_acquisition_due")
    ):
        op.create_index(
            "ix_ulk_acquisition_due",
            "user_lemma_knowledge",
            ["acquisition_box", "acquisition_next_due"],
        )

    # Ensure idempotency keys have uniqueness guarantees.
    if _table_exists(bind, "review_log") and _column_exists(bind, "review_log", "client_review_id"):
        if not _has_unique_single_column_key(bind, "review_log", "client_review_id"):
            _dedupe_client_review_ids(bind, "review_log")
            if not _named_index_exists(bind, "review_log", "ux_review_log_client_review_id"):
                op.create_index(
                    "ux_review_log_client_review_id",
                    "review_log",
                    ["client_review_id"],
                    unique=True,
                )

    if _table_exists(bind, "sentence_review_log") and _column_exists(bind, "sentence_review_log", "client_review_id"):
        if not _has_unique_single_column_key(bind, "sentence_review_log", "client_review_id"):
            _dedupe_client_review_ids(bind, "sentence_review_log")
            if not _named_index_exists(bind, "sentence_review_log", "ux_sentence_review_log_client_review_id"):
                op.create_index(
                    "ux_sentence_review_log_client_review_id",
                    "sentence_review_log",
                    ["client_review_id"],
                    unique=True,
                )


def downgrade() -> None:
    # Intentional no-op: this migration reconciles drift and keeps schema at expected shape.
    pass
