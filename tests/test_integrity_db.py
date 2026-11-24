"""Phase 7.1 database integrity against a real Postgres: concurrent re-ingestion of one source never
duplicates chunks, a failed ingestion leaves the old content untouched (never a half-written source),
clearing a source in one saved graph keeps another graph's source with the same id, and the connection
pool recovers when the database drops its connections (as after a database restart).
Embeddings are faked, so Ollama is not needed. Skipped unless TEST_DATABASE_URL is set."""

import os
import threading

import numpy as np
import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a Postgres with pgvector")
if DB_URL:
    os.environ["DATABASE_URL"] = DB_URL


def unit(seed: int) -> list[float]:
    v = np.random.default_rng(seed).normal(size=1024)
    return list(v / np.linalg.norm(v))


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
        from app.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM chunks WHERE source_id LIKE 'int_%'")


def count(scope: str, source_id: str) -> int:
    from app.db import get_conn
    with get_conn() as conn:
        return conn.execute("SELECT count(*) FROM chunks WHERE scope = %s AND source_id = %s", (scope, source_id)).fetchone()[0]


def test_concurrent_reingestion_of_one_source_never_duplicates_chunks(client):
    from app.rag import store
    rows = [(1, f"chunk {i}") for i in range(40)]
    vectors = [unit(i) for i in range(40)]
    barrier = threading.Barrier(6)
    errors = []

    def ingest():
        try:
            barrier.wait()
            store.replace_source("int_conc", "source_doc", "same.pdf", rows, vectors, "graph:9001")
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=ingest) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert count("graph:9001", "int_conc") == 40  # one copy, not up to six


def test_failed_ingestion_keeps_the_previous_content(client, monkeypatch):
    from app.rag import embeddings, ingest, store
    store.replace_source("int_keep", "source_doc", "old.pdf", [(1, "old text")], [unit(1)], "graph:9002")

    def broken(texts):
        raise embeddings.EmbeddingError("embedding model is down")
    monkeypatch.setattr(embeddings, "embed", broken)
    with pytest.raises(embeddings.EmbeddingError):
        ingest._store("int_keep", "source_doc", "new.pdf", [(1, "new text")], "graph:9002")
    info = store.source_info("int_keep", "graph:9002")
    assert info["chunks"] == 1 and info["file"] == "old.pdf"


def test_insert_failure_rolls_back_the_delete(client):
    """If writing the new chunks fails half-way, the delete of the old ones is rolled back too."""
    from app.rag import store
    store.replace_source("int_tx", "source_doc", "old.pdf", [(1, "a"), (1, "b")], [unit(2), unit(3)], "graph:9003")
    with pytest.raises(Exception):
        store.replace_source("int_tx", "source_doc", "new.pdf", [(1, "c"), (1, "d")], [unit(4), [0.0] * 3], "graph:9003")
    info = store.source_info("int_tx", "graph:9003")
    assert info["chunks"] == 2 and info["file"] == "old.pdf"


def test_clearing_a_source_in_one_graph_keeps_the_other_graphs_copy(client):
    from app.rag import store
    store.replace_source("int_shared", "source_doc", "a.pdf", [(1, "A")], [unit(5)], "graph:9004")
    store.replace_source("int_shared", "source_doc", "b.pdf", [(1, "B")], [unit(6)], "graph:9005")
    assert store.clear_source("int_shared", "graph:9004") == 1
    assert count("graph:9004", "int_shared") == 0 and count("graph:9005", "int_shared") == 1


def test_pool_recovers_after_the_database_drops_its_connections(client):
    """A database restart kills every pooled connection; the next requests must still work."""
    import psycopg
    from accounts import Account
    from app.db import get_conn
    from app.main import app
    acct = Account(app, "pool_user")  # signed in before the drop: the requests after it are what is tested
    with get_conn() as conn:  # make sure the pool holds an open connection
        conn.execute("SELECT 1")
    import time
    others = ("FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
              "AND backend_type = 'client backend'")
    with psycopg.connect(DB_URL, autocommit=True) as admin:
        admin.execute(f"SELECT pg_terminate_backend(pid) {others}")
        for _ in range(50):  # termination is asynchronous; after a real restart the old sessions are gone
            if admin.execute(f"SELECT count(*) {others}").fetchone()[0] == 0:
                break
            time.sleep(0.1)
    # Each of these queries the database. A reconnect may take a moment (the Windows test server is slow to
    # accept new connections; on the Docker Postgres the first request already succeeds), but there is never
    # a bare 500: either the answer or a clear 503, and the pool recovers.
    for path in (f"/projects/{acct.project}/sources", "/usage/totals", f"/graphs?project_id={acct.project}"):
        deadline = time.monotonic() + 30  # the Windows test server can take several seconds per new connection
        while True:
            r = acct.get(path)
            assert r.status_code in (200, 503), (path, r.status_code, r.text)
            if r.status_code == 200 or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        assert r.status_code == 200, (path, r.text)


def test_database_outage_is_a_clear_503_not_a_bare_500(client, monkeypatch):
    import psycopg
    from accounts import Account
    from app import knowledge
    from app.main import app
    acct = Account(app, "outage_user")

    def down(project_id):
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(knowledge, "list_sources", down)
    r = acct.get(f"/projects/{acct.project}/sources")
    assert r.status_code == 503 and "database is not reachable" in r.json()["detail"]
