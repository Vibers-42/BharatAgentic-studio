"""Phase 3 against a real Postgres with pgvector: source isolation in search, HNSW recall with a
filter, and the PDF upload endpoint. Embeddings are faked, so Ollama is not needed.
Skipped unless TEST_DATABASE_URL is set."""

import os

import numpy as np
import pytest

from pdfgen import REFUND_POLICY, make_pdf

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a Postgres with pgvector")

if DB_URL:
    os.environ["DATABASE_URL"] = DB_URL


def unit(seed: int) -> list[float]:
    v = np.random.default_rng(seed).normal(size=1024)
    return list(v / np.linalg.norm(v))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import dataclasses
    from fastapi.testclient import TestClient
    from app import knowledge
    from app.main import app
    from app.routes import sources
    # Settings is loaded once per process, so point the upload code at a temp folder directly.
    original = knowledge.settings
    temp = dataclasses.replace(original, upload_dir=str(tmp_path_factory.mktemp("uploads")))
    knowledge.settings = sources.settings = temp
    with TestClient(app) as c:
        yield c
    knowledge.settings = sources.settings = original


@pytest.fixture(scope="module")
def user(client):
    from accounts import Account
    from app.main import app
    return Account(app, "p3_user")


@pytest.fixture
def store(client):
    from app.rag import store as s
    yield s
    from app.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM chunks WHERE source_id LIKE 'test_%'")


def test_search_never_returns_unconnected_source(store):
    query = unit(1)
    store.replace_source("test_a", "source_doc", "a.pdf", [(1, "A text")], [unit(2)])
    store.replace_source("test_b", "source_doc", "b.pdf", [(1, "B text")], [query])  # a perfect match
    found = store.search(query, ["test_a"], 5)
    assert [c.source_id for c in found] == ["test_a"]
    assert store.search(query, [], 5) == []


def test_filtered_search_keeps_recall_with_many_other_chunks(store):
    """300 closer chunks in another source must not push the connected chunk out of the results."""
    query = unit(10)
    near = [list(np.array(query) + np.random.default_rng(i).normal(scale=0.01, size=1024)) for i in range(300)]
    store.replace_source("test_noise", "source_doc", "noise.pdf", [(1, f"n{i}") for i in range(300)], near)
    store.replace_source("test_target", "source_doc", "target.pdf", [(1, "target")], [unit(11)])
    found = store.search(query, ["test_target"], 5)
    assert [c.content for c in found] == ["target"]


def test_replace_source_replaces_old_chunks(store):
    store.replace_source("test_r", "source_doc", "old.pdf", [(1, "x"), (1, "y")], [unit(3), unit(4)])
    store.replace_source("test_r", "source_doc", "new.pdf", [(2, "z")], [unit(5)])
    info = store.source_info("test_r")
    assert info["chunks"] == 1 and info["file"] == "new.pdf"


def test_upload_pdf_ingests_with_pages(user, monkeypatch):
    from app.rag import embeddings
    monkeypatch.setattr(embeddings, "embed", lambda texts: [unit(i) for i in range(len(texts))])
    r = user.add_pdf(user.project, "refund-policy.pdf", make_pdf(REFUND_POLICY))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ready" and body["chunk_count"] >= 2
    assert user.get(f"/sources/{body['id']}").json()["chunk_count"] == body["chunk_count"]
    from app.db import get_conn
    with get_conn() as conn:
        pages = {row[0] for row in conn.execute("SELECT page FROM chunks WHERE source_ref = %s", (body["id"],))}
    assert pages == {1, 2}


def test_upload_rejects_non_pdf(user):
    r = user.add_pdf(user.project, "notes.pdf", b"hello, not a pdf")
    assert r.status_code == 400
    r = user.post(f"/projects/{user.project}/sources/file", files={"file": ("notes.exe", b"%PDF-1.4", "text/plain")})
    assert r.status_code == 400


def test_upload_to_an_unknown_project_is_404(user):
    assert user.add_pdf(999999, "a.pdf", make_pdf(REFUND_POLICY)).status_code == 404


def test_upload_file_name_cannot_escape_folder(user, monkeypatch):
    from app import knowledge
    from app.rag import embeddings
    monkeypatch.setattr(embeddings, "embed", lambda texts: [unit(i) for i in range(len(texts))])
    r = user.add_pdf(user.project, "../../evil.pdf", make_pdf(REFUND_POLICY))
    assert r.status_code == 201 and r.json()["title"] == "evil.pdf"
    from pathlib import Path
    root = Path(knowledge.settings.upload_dir)
    assert (root / f"project_{user.project}" / f"source_{r.json()['id']}" / "evil.pdf").exists()
    assert not (root.parent / "evil.pdf").exists()
    r = user.add_pdf(user.project, r"..\..\win.pdf", make_pdf(REFUND_POLICY))
    assert r.status_code == 201 and r.json()["title"] == "win.pdf"


def test_scanned_pdf_without_text_is_422_and_leaves_no_file(user):
    from app import knowledge
    from pathlib import Path
    r = user.add_pdf(user.project, "scan.pdf", make_pdf([[]]))
    assert r.status_code == 422 and "no extractable text" in r.json()["detail"]
    failed = [s for s in user.get(f"/projects/{user.project}/sources").json() if s["title"] == "scan.pdf"]
    assert failed[0]["status"] == "failed" and failed[0]["chunk_count"] == 0
    folder = Path(knowledge.settings.upload_dir) / f"project_{user.project}" / f"source_{failed[0]['id']}"
    assert not any(folder.glob("*")) if folder.exists() else True  # the refused file is not kept
