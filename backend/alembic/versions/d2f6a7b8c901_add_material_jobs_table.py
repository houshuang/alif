"""Add material job coordinator table.

Revision ID: d2f6a7b8c901
Revises: c9a0b1c2d345
Create Date: 2026-05-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2f6a7b8c901"
down_revision: Union[str, Sequence[str], None] = "c9a0b1c2d345"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "material_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("not_before", sa.DateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_material_jobs_kind", "material_jobs", ["kind"])
    op.create_index("ix_material_jobs_status", "material_jobs", ["status"])
    op.create_index("ix_material_jobs_priority", "material_jobs", ["priority"])
    op.create_index("ix_material_jobs_dedupe_key", "material_jobs", ["dedupe_key"])
    op.create_index("ix_material_jobs_not_before", "material_jobs", ["not_before"])
    op.create_index("ix_material_jobs_lease_until", "material_jobs", ["lease_until"])
    op.create_index("ix_material_jobs_created_at", "material_jobs", ["created_at"])
    op.create_index(
        "ix_material_jobs_claim",
        "material_jobs",
        ["status", "not_before", "priority", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_material_jobs_claim", table_name="material_jobs")
    op.drop_index("ix_material_jobs_created_at", table_name="material_jobs")
    op.drop_index("ix_material_jobs_lease_until", table_name="material_jobs")
    op.drop_index("ix_material_jobs_not_before", table_name="material_jobs")
    op.drop_index("ix_material_jobs_dedupe_key", table_name="material_jobs")
    op.drop_index("ix_material_jobs_priority", table_name="material_jobs")
    op.drop_index("ix_material_jobs_status", table_name="material_jobs")
    op.drop_index("ix_material_jobs_kind", table_name="material_jobs")
    op.drop_table("material_jobs")
