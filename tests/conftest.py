"""Configuration des tests : une base PostgreSQL de test (jamais celle du développement), créée au besoin et migrée par Alembic.

Les tests s'exécutent dans le conteneur du serveur (voir README, « Tests ») : la base « db » y est joignable.
"""

import os
from urllib.parse import urlsplit, urlunsplit

import pytest

# Avant tout import de l'application : elle lit DATABASE_URL à l'import
_base = os.environ.get("DATABASE_URL", "postgresql://buildsen:buildsen@db:5432/buildsen")
_parts = urlsplit(_base)
TEST_URL = urlunsplit((_parts.scheme, _parts.netloc, "/buildsen_test", "", ""))
ADMIN_URL = urlunsplit(("postgresql+psycopg", _parts.netloc, "/postgres", "", ""))
os.environ["DATABASE_URL"] = TEST_URL
os.environ["ENVIRONMENT"] = "development"
os.environ["WHATSAPP_PROVIDER"] = "sim"
os.environ.pop("RESEND_API_KEY", None)
os.environ["PLANS_ENABLED"] = "false"  # les formules sont activées test par test (fixture plans_on)
os.environ["PAYMENT_PROVIDER"] = "sim"
os.environ["LAN_URL"] = ""
os.environ["SOYNADE_API_KEY"] = "test-key"  # jamais appelée : la voix est simulée dans les tests


def _create_database() -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        if not c.execute(text("select 1 from pg_database where datname = 'buildsen_test'")).scalar():
            c.execute(text("create database buildsen_test"))
    engine.dispose()


@pytest.fixture(scope="session")
def migrated():
    """Base de test créée puis migrée une fois pour toute la session de tests."""
    _create_database()
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    return True


@pytest.fixture
def db(migrated):
    """Session vide à chaque test : toutes les tables sont vidées avant."""
    from sqlalchemy import text

    from app.db import SessionLocal, engine

    with engine.begin() as c:
        names = c.execute(text("select tablename from pg_tables where schemaname = 'public' and tablename <> 'alembic_version'")).scalars().all()
        if names:
            c.execute(text("truncate " + ", ".join(f'"{n}"' for n in names) + " restart identity cascade"))
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
