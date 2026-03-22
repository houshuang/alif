"""add story audio/archive fields and times_heard

Revision ID: c261385e96be
Revises: c97ecd42b7f1
Create Date: 2026-03-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c261385e96be'
down_revision: Union[str, Sequence[str], None] = 'c97ecd42b7f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add story audio/archive fields and times_heard to ULK."""
    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('format_type', sa.String(30), server_default='standard', nullable=True))
        batch_op.add_column(sa.Column('archived_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('audio_filename', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('voice_id', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('metadata_json', sa.JSON(), nullable=True))

    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.add_column(sa.Column('times_heard', sa.Integer(), server_default='0', nullable=True))


def downgrade() -> None:
    """Remove story audio/archive fields and times_heard."""
    with op.batch_alter_table('user_lemma_knowledge', schema=None) as batch_op:
        batch_op.drop_column('times_heard')

    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.drop_column('metadata_json')
        batch_op.drop_column('voice_id')
        batch_op.drop_column('audio_filename')
        batch_op.drop_column('archived_at')
        batch_op.drop_column('format_type')
