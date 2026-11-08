"""Paiement des formules : table Payment (nouvelle table uniquement, aucune table existante modifiée).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "Payment",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("userId", sa.Text, sa.ForeignKey("User.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan", sa.Text, nullable=False),
        sa.Column("months", sa.Integer, nullable=False, server_default="1"),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("token", sa.Text, nullable=False, unique=True),
        sa.Column("state", sa.Text, nullable=False, server_default="PENDING"),
        sa.Column("source", sa.Text, nullable=False, server_default="web"),
        sa.Column("checkoutUrl", sa.Text, nullable=False, server_default=""),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("paidAt", sa.DateTime),
        sa.Column("createdAt", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("Payment_userId_createdAt_idx", "Payment", ["userId", "createdAt"])


def downgrade() -> None:
    op.drop_index("Payment_userId_createdAt_idx", table_name="Payment")
    op.drop_table("Payment")
