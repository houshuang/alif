"""add client_review_id

Revision ID: b3e7d9a1f402
Revises: affea1c30b95
Create Date: 2026-02-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e7d9a1f402'
down_revision: Union[str, Sequence[str], None] = 'affea1c30b95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('review_log') as batch_op:
        batch_op.add_column(sa.Column('client_review_id', sa.String(50), nullable=True))
        batch_op.create_unique_constraint('uq_review_log_client_review_id', ['client_review_id'])

    with op.batch_alter_table('sentence_review_log') as batch_op:
        batch_op.add_column(sa.Column('client_review_id', sa.String(50), nullable=True))
        batch_op.create_unique_constraint('uq_sentence_review_log_client_review_id', ['client_review_id'])


def downgrade() -> None:
    with op.batch_alter_table('sentence_review_log') as batch_op:
        batch_op.drop_constraint('uq_sentence_review_log_client_review_id', type_='unique')
        batch_op.drop_column('client_review_id')

    with op.batch_alter_table('review_log') as batch_op:
        batch_op.drop_constraint('uq_review_log_client_review_id', type_='unique')
        batch_op.drop_column('client_review_id')
