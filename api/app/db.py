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
    from . import migrations  # versioned changes on top of the baseline schema (Phase 8 onwards)
    migrations.run(settings.database_url, settings.upload_dir)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        reset_interrupted_sources(conn)
    # check: a pooled connection is tested before it is handed out, so connections killed by a database
    # restart are replaced instead of failing the next requests.
    pool = ConnectionPool(
        settings.database_url, min_size=1, max_size=5, open=True, configure=register_vector,
        check=ConnectionPool.check_connection, timeout=10,  # wait at most 10 s for a free connection
    )
    pool.wait(timeout=10)  # fail at startup, not on the first request, if the db is unreachable


def reset_interrupted_sources(conn) -> int:
    """At startup: an ingestion cut off by a restart never committed chunks (they commit together with
    "ready"), but its row would say "processing" forever. One api process serves the stack, so at startup
    nothing else can still be working on it. Returns how many sources were reset."""
    return conn.execute(
        "UPDATE sources SET status = 'failed', chunk_count = 0, updated_at = now(), "
        "error = 'Interrupted by an api restart. Re-index it or add it again.' WHERE status = 'processing'").rowcount


def close_db() -> None:
    if pool is not None:
        pool.close()


def get_conn():
    if pool is None:
        raise RuntimeError("Database pool is not initialised")
    return pool.connection()
