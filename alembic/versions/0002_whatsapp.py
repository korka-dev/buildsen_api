"""Assistant WhatsApp : sessions, journal des messages, codes de connexion, réglages.

Ajoute seulement de nouvelles tables : aucune table existante n'est modifiée, la migration est donc sans risque
sur une base déjà en service (`alembic upgrade head` après `alembic stamp 0001`).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "WaSession",
        sa.Column("phone", sa.Text, primary_key=True),
        sa.Column("state", sa.Text, nullable=False, server_default="NEW"),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("userId", sa.Text, sa.ForeignKey("User.id", ondelete="SET NULL")),
        sa.Column("optedOut", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("lastInboundAt", sa.DateTime),
        sa.Column("createdAt", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "WaMessage",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("phone", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.Text, nullable=False, server_default="sent"),
        sa.Column("payload", JSONB),
        sa.Column("createdAt", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("WaMessage_phone_createdAt_idx", "WaMessage", ["phone", "createdAt"])
    op.create_table(
        "WaCode",
        sa.Column("phone", sa.Text, primary_key=True),
        sa.Column("codeHash", sa.Text, nullable=False),
        sa.Column("expiresAt", sa.DateTime, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "AppSetting",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("AppSetting")
    op.drop_table("WaCode")
    op.drop_index("WaMessage_phone_createdAt_idx", table_name="WaMessage")
    op.drop_table("WaMessage")
    op.drop_table("WaSession")
