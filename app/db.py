"""Connexion à PostgreSQL avec SQLAlchemy 2 (mode synchrone : FastAPI exécute les routes dans un pool de threads)."""

from collections.abc import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

# Paramètres propres à Prisma, refusés par le pilote psycopg
_PRISMA_ONLY = {"pgbouncer", "connection_limit", "pool_timeout", "schema", "socket_timeout"}


def sqlalchemy_url(url: str) -> str:
    """Adapte une adresse « postgresql://… » (format Prisma / Neon) au pilote psycopg 3."""
    parts = urlsplit(url)
    scheme = "postgresql+psycopg" if parts.scheme in ("postgres", "postgresql") else parts.scheme
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k not in _PRISMA_ONLY])
    return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))


engine = create_engine(
    sqlalchemy_url(settings.database_url),
    pool_pre_ping=True,  # Neon met la base en veille : on vérifie la connexion avant de la réutiliser
    pool_size=5,
    max_overflow=5,
    pool_recycle=300,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
