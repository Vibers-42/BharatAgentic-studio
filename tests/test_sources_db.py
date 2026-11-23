"""Phase 5 against a real Postgres: chunks of different saved graphs stay apart even with the same source
id, docs URLs and GitHub repos are ingested, and internal addresses are refused. Embeddings are faked
(no Ollama) and pages are served locally. Skipped unless TEST_DATABASE_URL is set."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from pdfgen import HOSTEL_RULES, REFUND_POLICY, make_pdf

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a Postgres with pgvector")
if DB_URL:
    os.environ["DATABASE_URL"] = DB_URL

PAGE = b"""<html><head><title>Clinic FAQ</title></head><body><nav>menu</nav>
<h1>Timings</h1><p>The clinic is open from 8 AM to 8 PM on weekdays.</p></body></html>"""


def vector_for(text: str) -> list[float]:
    """Deterministic fake embedding: texts about the same topic word point the same way."""
    v = np.zeros(1024, dtype=np.float32)
    for i, word in enumerate(["refund", "hostel", "clinic", "readme"]):
        if word in text.lower():
            v[i] = 1.0
    v[1023] = 0.1
    return list(v / np.linalg.norm(v))


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
        from app.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM chunks WHERE source_id LIKE 't5_%' OR scope LIKE 'graph:%' AND source_id = 's1'")


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """Fake embeddings, but the real retrieve() (conftest replaces it by default), so searches hit Postgres."""
    from app.pipeline.stages import agent
    from app.rag import embeddings
    from app.rag.retrieve import retrieve
    monkeypatch.setattr(embeddings, "embed", lambda texts: [vector_for(t) for t in texts])
    monkeypatch.setattr(embeddings, "embed_one", vector_for)
    monkeypatch.setattr(agent, "retrieve", retrieve)


@pytest.fixture
def page_server(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/faq")
                self.end_headers()
                return
            body, ctype = (PAGE, "text/html; charset=utf-8") if self.path == "/faq" else (make_pdf(HOSTEL_RULES), "application/pdf")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ALLOW_PRIVATE_URLS", "true")  # the test page is on 127.0.0.1
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def save_graph(client, full_graph, name):
    keep = {"n1", "n2", "n3", "n4", "n5", "s1"}
    g = {"nodes": [n for n in full_graph["nodes"] if n["id"] in keep],
         "edges": [e for e in full_graph["edges"] if e["from"] in keep]}
    g["nodes"][-1] = {"id": "s1", "type": "source_doc", "config": {"file": f"{name}.pdf"}}
    r = client.post("/graphs", json={"name": name, "graph": g})
    assert r.status_code == 201
    return r.json()["id"]


def searched(client, graph_id, question):
    r = client.post("/pipeline/run?stream=false", json={"question": question, "graph_id": graph_id})
    agent = next(e for e in r.json()["events"] if e.get("stage") == "agent" and e.get("status") == "done")
    return agent["detail"]["retrieval"]


def test_same_source_id_in_two_saved_graphs_stays_apart(client, full_graph):
    a = save_graph(client, full_graph, "clinic")
    b = save_graph(client, full_graph, "hostel")
    pdf_a = {"file": ("refund-policy.pdf", make_pdf(REFUND_POLICY), "application/pdf")}
    pdf_b = {"file": ("hostel-rules.pdf", make_pdf(HOSTEL_RULES), "application/pdf")}
    assert client.post(f"/sources/s1/pdf?graph_id={a}", files=pdf_a).status_code == 201
    assert client.post(f"/sources/s1/pdf?graph_id={b}", files=pdf_b).status_code == 201

    ra = searched(client, a, "hostel gate timings")
    rb = searched(client, b, "hostel gate timings")
    assert ra["scope"] == f"graph:{a}" and rb["scope"] == f"graph:{b}"
    assert {c["file"] for c in ra["candidates"]} == {"refund-policy.pdf"}
    assert {c["file"] for c in rb["candidates"]} == {"hostel-rules.pdf"}
    # an inline graph using "s1" sees neither of them
    assert client.get("/sources/s1").json()["chunks"] == 0


def test_upload_to_a_node_the_graph_does_not_have_is_refused(client, full_graph):
    a = save_graph(client, full_graph, "clinic2")
    pdf = {"file": ("x.pdf", make_pdf(REFUND_POLICY), "application/pdf")}
    assert client.post(f"/sources/s9/pdf?graph_id={a}", files=pdf).status_code == 404
    assert client.post(f"/sources/n4/pdf?graph_id={a}", files=pdf).status_code == 400  # n4 is the agent


def test_docs_url_is_ingested_with_title_and_link(client, page_server):
    r = client.post("/sources/t5_url/url", json={"url": f"{page_server}/moved"})  # follows the redirect
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "url" and body["file"] == "Clinic FAQ" and body["url"].endswith("/faq")
    from app.rag import store
    found = store.search(vector_for("clinic timings"), ["t5_url"], 3)
    assert found[0].origin.endswith("/faq") and "8 AM to 8 PM" in found[0].content and "menu" not in found[0].content


def test_pdf_link_is_ingested_as_pdf(client, page_server):
    r = client.post("/sources/t5_pdflink/url", json={"url": f"{page_server}/rules.pdf"})
    assert r.status_code == 201 and r.json()["chunks"] >= 1


def test_internal_address_is_refused(client, monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    for url in ["http://127.0.0.1:8000/health", "http://ollama:11434/api/tags", "http://169.254.169.254/latest/meta-data"]:
        r = client.post("/sources/t5_bad/url", json={"url": url})
        assert r.status_code in (400, 422), (url, r.status_code)
        if r.status_code == 400:
            assert "private or internal" in r.json()["detail"] or "Cannot find" in r.json()["detail"]
    assert client.post("/sources/t5_bad/url", json={"url": "file:///etc/passwd"}).status_code == 400


def test_github_repo_is_ingested_per_file(client, monkeypatch):
    from test_sources_units import make_zip
    from app.rag import fetch, repo
    data = make_zip({"README.md": b"# Readme\nThis readme explains the demo.", "src/main.py": b"print('readme')"})
    monkeypatch.setattr(repo, "fetch", lambda url, max_bytes, headers=None: fetch.Fetched(url, "application/zip", data))
    r = client.post("/sources/t5_repo/repo", json={"url": "https://github.com/octocat/demo"})
    assert r.status_code == 201, r.text
    assert r.json()["files"] == 2 and r.json()["repo"] == "octocat/demo@abc1234"
    from app.rag import store
    found = store.search(vector_for("readme"), ["t5_repo"], 5)
    assert {c.file_name for c in found} == {"README.md", "src/main.py"}
    assert all(c.content.startswith("File: ") and c.origin.startswith("https://github.com/octocat/demo/blob/") for c in found)


def test_bad_repo_link_is_a_clear_error(client):
    r = client.post("/sources/t5_repo/repo", json={"url": "https://example.com/not-github"})
    assert r.status_code == 422 and "GitHub repository link" in r.json()["detail"]


def test_saved_graph_ingests_its_link_sources(client, full_graph, page_server):
    g = {"nodes": [n for n in full_graph["nodes"] if n["type"] in ("input", "guardrails", "router", "agent", "output")]
         + [{"id": "s1", "type": "source_doc", "config": {"url": f"{page_server}/faq"}},
            {"id": "s2", "type": "source_doc", "config": {"file": "later.pdf"}}],
         "edges": [e for e in full_graph["edges"] if e["from"] in ("n1", "n2", "n3", "n4")]
         + [{"from": "s1", "to": "n4"}, {"from": "s2", "to": "n4"}]}
    gid = client.post("/graphs", json={"name": "links", "graph": g}).json()["id"]
    out = client.post(f"/graphs/{gid}/ingest").json()
    by = {s["node"]: s for s in out["sources"]}
    assert by["s1"]["ok"] and by["s1"]["scope"] == f"graph:{gid}" and by["s1"]["chunks"] >= 1
    assert by["s2"]["ok"] is False and "upload a PDF" in by["s2"]["error"]
    assert client.get(f"/sources/s1?graph_id={gid}").json()["chunks"] >= 1


# ---------- Phase 6: the canvas ----------
def test_canvas_graph_runs_against_its_saved_graph_documents(client, full_graph):
    """The canvas sends its current (unsaved) graph plus the saved graph id: the edited graph runs,
    and only that saved graph's documents are searched."""
    gid = save_graph(client, full_graph, "canvas")
    pdf = {"file": ("hostel-rules.pdf", make_pdf(HOSTEL_RULES), "application/pdf")}
    assert client.post(f"/sources/s1/pdf?graph_id={gid}", files=pdf).status_code == 201
    edited = client.get(f"/graphs/{gid}").json()
    edited["nodes"][3]["config"]["language"] = "hi"  # an unsaved edit on the canvas
    r = client.post("/pipeline/run?stream=false", json={"question": "hostel gate", "graph": edited, "graph_id": gid})
    agent = next(e for e in r.json()["events"] if e.get("stage") == "agent" and e.get("status") == "done")
    assert agent["detail"]["retrieval"]["scope"] == f"graph:{gid}"
    assert {c["file"] for c in agent["detail"]["retrieval"]["candidates"]} == {"hostel-rules.pdf"}
    assert r.json()["events"][-1]["language"] == "hi"  # the edited graph ran, not the saved one


def test_run_with_unknown_graph_id_is_404(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph, "graph_id": 999999})
    assert r.status_code == 404


def test_update_saved_graph_keeps_id_documents_and_positions(client, full_graph):
    gid = save_graph(client, full_graph, "layout")
    g = client.get(f"/graphs/{gid}").json()
    assert all("position" not in n for n in g["nodes"])  # graphs without positions stay as they were
    for i, n in enumerate(g["nodes"]):
        n["position"] = {"x": 100 * i, "y": 50}
    r = client.put(f"/graphs/{gid}", json={"name": "layout v2", "graph": g})
    assert r.status_code == 200 and r.json() == {"id": gid, "name": "layout v2"}
    again = client.get(f"/graphs/{gid}").json()
    assert [n["position"] for n in again["nodes"]] == [{"x": 100 * i, "y": 50} for i in range(len(g["nodes"]))]
    assert next(x for x in client.get("/graphs").json() if x["id"] == gid)["name"] == "layout v2"


def test_update_refuses_an_invalid_graph_and_unknown_id(client, full_graph):
    gid = save_graph(client, full_graph, "strict")
    g = client.get(f"/graphs/{gid}").json()
    g["edges"] = [e for e in g["edges"] if e["from"] != "s1"]  # source no longer connected
    r = client.put(f"/graphs/{gid}", json={"name": "broken", "graph": g})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "not_connected"
    assert client.put("/graphs/999999", json={"name": "x", "graph": full_graph}).status_code == 404
