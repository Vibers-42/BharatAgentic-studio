"""Versioned database migrations. schema.sql is the baseline (applied on every start, idempotent); each module
here named m<NNNN>_<name>.py has an `upgrade(conn, upload_dir)` function and runs exactly once, in its own
transaction, recorded in schema_migrations. A transaction-level advisory lock makes concurrent starts safe:
a second api process waits and then finds the migration already applied."""

import importlib
import pkgutil

import psycopg

LOCK_KEY = 7_202_611  # any constant; only migrations use it


def pending_modules():
    names = sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name.startswith("m"))
    return [(n.split("_", 1)[0][1:], n) for n in names]


def run(database_url: str, upload_dir: str) -> list[str]:
    """Apply every migration not applied yet. Returns the versions applied now."""
    applied_now = []
    with psycopg.connect(database_url) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            version TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        conn.commit()
        for version, name in pending_modules():
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,)).fetchone():
                    continue
                importlib.import_module(f"{__name__}.{name}").upgrade(conn, upload_dir)
                conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
                applied_now.append(version)
    return applied_now
