"""Word add contexts: whether the add started the word, and how a wrong entry was resolved."""
from alembic import op
import sqlalchemy as sa

revision = "c3e5a7b9d1f4"
down_revision = "b8d0f2a4c6e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("word_add_contexts") as b:
        b.add_column(sa.Column("introduced_by_add", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("resolution", sa.String(20), nullable=True))
        b.add_column(sa.Column("resolved_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("word_add_contexts") as b:
        b.drop_column("resolved_at")
        b.drop_column("resolution")
        b.drop_column("introduced_by_add")
