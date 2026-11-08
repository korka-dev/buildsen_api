"""Avis des clients : table Review (nouvelle table uniquement).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "Review",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("conversationId", sa.Text, sa.ForeignKey("Conversation.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("proId", sa.Text, sa.ForeignKey("User.id", ondelete="CASCADE"), nullable=False),
        sa.Column("clientId", sa.Text, sa.ForeignKey("User.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("state", sa.Text, nullable=False, server_default="VISIBLE"),
        sa.Column("createdAt", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="Review_rating_check"),
    )
    op.create_index("Review_proId_createdAt_idx", "Review", ["proId", "createdAt"])


def downgrade() -> None:
    op.drop_index("Review_proId_createdAt_idx", table_name="Review")
    op.drop_table("Review")
