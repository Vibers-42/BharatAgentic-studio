"""Creates the tables on startup and holds one shared Postgres connection pool.
Use `with get_conn() as conn:` anywhere a query is needed."""

from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from .config import settings

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

pool: ConnectionPool | None = None


def init_db() -> None:
    """Run schema.sql once, then open the pool. The vector extension must exist
    before register_vector can teach each pooled connection the vector type."""
    global pool
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    pool = ConnectionPool(
        settings.database_url, min_size=1, max_size=5, open=True, configure=register_vector
    )
    pool.wait(timeout=10)  # fail at startup, not on the first request, if the db is unreachable


def close_db() -> None:
    if pool is not None:
        pool.close()


def get_conn():
    if pool is None:
        raise RuntimeError("Database pool is not initialised")
    return pool.connection()
