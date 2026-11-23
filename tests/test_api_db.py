"""Runs the real API against a real Postgres with pgvector: schema creation, seed users,
vector column, and the /health and /status endpoints. Skipped unless TEST_DATABASE_URL is set."""

import os
import sys
from pathlib import Path

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a Postgres with pgvector")

if DB_URL:
    os.environ["DATABASE_URL"] = DB_URL
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:  # runs startup: schema.sql + pool
        yield c


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_status_reports_db_and_pgvector(client):
    body = client.get("/status").json()
    assert body["api"] is True
    assert body["db"] is True
    assert body["pgvector"]


def test_tables_and_seed_users_exist(client):
    from app.db import get_conn

    with get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
        users = {r[0] for r in conn.execute("SELECT name FROM users")}
    assert {"users", "graphs", "chunks", "usage"} <= tables
    assert {"demo", "aditya"} <= users


def test_vector_column_round_trip(client):
    """Insert a 1024-dim vector, find it by cosine distance, then roll back."""
    import numpy as np
    from app.db import get_conn

    vec = np.zeros(1024, dtype=np.float32)
    vec[0] = 1.0
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chunks (source_id, source_type, file_name, chunk_index, content, embedding)"
            " VALUES ('test', 'source_doc', 'x.pdf', 0, 'hello', %s)", (vec,))
        row = conn.execute(
            "SELECT content, embedding <=> %s AS dist FROM chunks WHERE source_id = 'test'", (vec,)
        ).fetchone()
        conn.rollback()
    assert row[0] == "hello"
    assert row[1] == pytest.approx(0.0)


def test_save_load_and_run_saved_graph(client, full_graph):
    r = client.post("/graphs", json={"name": "test pipeline", "graph": full_graph})
    assert r.status_code == 201
    graph_id = r.json()["id"]

    assert client.get(f"/graphs/{graph_id}").json() == full_graph  # "from" keys survive the round trip
    assert any(g["id"] == graph_id for g in client.get("/graphs").json())

    run = client.post("/pipeline/run?stream=false", json={"graph_id": graph_id, "question": "refund policy?"})
    assert run.json()["events"][-1]["sources"] == ["s1", "s2"]


def test_invalid_graph_is_not_saved(client, full_graph):
    full_graph["edges"] = [e for e in full_graph["edges"] if e["from"] != "t1"]
    r = client.post("/graphs", json={"name": "broken", "graph": full_graph})
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["node_id"] == "t1"


def test_unknown_graph_id_is_404(client):
    assert client.get("/graphs/999999").status_code == 404
    r = client.post("/pipeline/run", json={"graph_id": 999999, "question": "hi"})
    assert r.status_code == 404


def test_schema_is_safe_to_rerun(client):
    from app.db import init_db, close_db

    close_db()
    init_db()  # second run must not fail or duplicate seed users
    from app.db import get_conn

    with get_conn() as conn:
        count = conn.execute("SELECT count(*) FROM users WHERE name = 'demo'").fetchone()[0]
    assert count == 1
