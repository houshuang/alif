"""Keep supported reading evidence separate from word review judgments."""
from alembic import op
import sqlalchemy as sa

revision = "b8d0f2a4c6e8"
down_revision = "a7c9e1f3b5d7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reading_pilot_events",
        sa.Column("client_event_id", sa.String(100), primary_key=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("reading_pilot_events")
