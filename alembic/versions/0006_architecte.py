"""Statut « Architecte » : nouvelle valeur de l'énumération ProStatut.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():  # une valeur d'énumération ne s'ajoute pas dans une transaction ouverte sur les anciennes versions de PostgreSQL
        op.execute("ALTER TYPE \"ProStatut\" ADD VALUE IF NOT EXISTS 'ARCH'")


def downgrade() -> None:
    pass  # PostgreSQL ne sait pas retirer une valeur d'énumération ; elle reste sans effet
