"""Schéma de départ : reprise à l'identique des migrations SQL de la version Prisma (alembic/sql/*.sql).

Base neuve (développement, Docker) : `alembic upgrade head` crée toutes les tables.
Base existante (production Neon, déjà créée par Prisma) : NE PAS lancer upgrade ; marquer le point de départ avec
`alembic stamp 0001`. Les migrations suivantes s'appliqueront ensuite normalement.

Revision ID: 0001
Revises:
Create Date: 2026-10-19
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _statements(sql: str) -> list[str]:
    # Les fichiers ne contiennent que des instructions DDL simples, terminées par « ; » en fin de ligne
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";\n") if s.strip().rstrip(";")]


def upgrade() -> None:
    bind = op.get_bind()
    for f in sorted(SQL_DIR.glob("*.sql")):
        for statement in _statements(f.read_text(encoding="utf-8")):
            bind.exec_driver_sql(statement.rstrip(";"))


def downgrade() -> None:
    raise RuntimeError("Le schéma de départ ne se défait pas.")
