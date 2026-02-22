"""Add textbook_page_number to page_uploads

Revision ID: 3d88231674a2
Revises: x4e5f6g7h890
Create Date: 2026-02-22 09:05:15.955981

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d88231674a2'
down_revision: Union[str, Sequence[str], None] = 'x4e5f6g7h890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('page_uploads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('textbook_page_number', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('page_uploads', schema=None) as batch_op:
        batch_op.drop_column('textbook_page_number')
