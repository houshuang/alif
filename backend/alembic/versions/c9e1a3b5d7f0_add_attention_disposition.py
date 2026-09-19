"""Separate attention eligibility from remembered knowledge."""
from alembic import op
import sqlalchemy as sa
revision = "c9e1a3b5d7f0"
down_revision = "b8d0f2a4c6e8"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("user_lemma_knowledge", sa.Column("attention_disposition", sa.String(20), nullable=False, server_default="maintain"))
    op.add_column("user_lemma_knowledge", sa.Column("attention_reason", sa.Text(), nullable=True))
    op.add_column("user_lemma_knowledge", sa.Column("attention_updated_at", sa.DateTime(), nullable=True))
    op.create_index("ix_user_lemma_knowledge_attention_disposition", "user_lemma_knowledge", ["attention_disposition"])

def downgrade():
    op.drop_index("ix_user_lemma_knowledge_attention_disposition", table_name="user_lemma_knowledge")
    with op.batch_alter_table("user_lemma_knowledge") as batch:
        batch.drop_column("attention_updated_at")
        batch.drop_column("attention_reason")
        batch.drop_column("attention_disposition")
