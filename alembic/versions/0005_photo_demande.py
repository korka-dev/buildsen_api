"""Photo du terrain ou croquis sur la demande : deux colonnes facultatives.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ServiceRequest", sa.Column("photoPath", sa.Text))
    op.add_column("ServiceRequest", sa.Column("photoMime", sa.Text))


def downgrade() -> None:
    op.drop_column("ServiceRequest", "photoMime")
    op.drop_column("ServiceRequest", "photoPath")
