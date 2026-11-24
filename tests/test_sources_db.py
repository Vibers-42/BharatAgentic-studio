"""Phase 5 sources, under Phase 8 projects, against a real Postgres: a project's knowledge stays apart from every
other project's (even with the same node ids), docs URLs and GitHub repos are ingested into the project registry,
internal addresses are refused, and agents (saved and unsaved) run only against their project's ready sources.
Embeddings are faked (no Ollama) and pages are served locally. Skipped unless TEST_DATABASE_URL is set."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from accounts import Account, agent_graph, fake_embeddings, vector_for
from pdfgen import HOSTEL_RULES, REFUND_POLICY, make_pdf

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a Postgres with pgvector")
if DB_URL:
    os.environ["DATABASE_URL"] = DB_URL

PAGE = b"""<html><head><title>Clinic FAQ</title></head><body><nav>menu</nav>
<h1>Timings</h1><p>The clinic is open from 8 AM to 8 PM on weekdays.</p></body></html>"""


@pytest.fixture(scope="module")
def app_client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def user(app_client):
    from app.main import app
    return Account(app, "p5_user")


@pytest.fixture(autouse=True)
def embeddings(monkeypatch):
    fake_embeddings(monkeypatch)


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


def new_project(acct, name):
    r = acct.post("/projects", json={"org_id": acct.personal, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def searched(acct, graph_id, question):
    r = acct.post("/pipeline/run?stream=false", json={"question": question, "graph_id": graph_id})
    assert r.status_code == 200, r.text
    agent = next(e for e in r.json()["events"] if e.get("stage") == "agent" and e.get("status") == "done")
    return agent["detail"]["retrieval"]


def test_same_node_id_in_two_projects_stays_apart(user):
    a, b = new_project(user, "clinic"), new_project(user, "hostel")
    sa = user.add_pdf(a, "refund-policy.pdf", make_pdf(REFUND_POLICY)).json()
    sb = user.add_pdf(b, "hostel-rules.pdf", make_pdf(HOSTEL_RULES)).json()
    assert sa["status"] == "ready" and sa["chunk_count"] >= 1 and sa["uploaded_by_name"] == user.name
    ga = user.post("/graphs", json={"project_id": a, "name": "A", "graph": agent_graph({"s1": sa["id"]})}).json()["id"]
    gb = user.post("/graphs", json={"project_id": b, "name": "B", "graph": agent_graph({"s1": sb["id"]})}).json()["id"]
    ra, rb = searched(user, ga, "hostel gate timings"), searched(user, gb, "hostel gate timings")
    assert ra["scope"] == f"project:{a}" and rb["scope"] == f"project:{b}"
    assert {c["file"] for c in ra["candidates"]} == {"refund-policy.pdf"}
    assert {c["file"] for c in rb["candidates"]} == {"hostel-rules.pdf"}


def test_an_agent_cannot_point_at_another_projects_source(user):
    a, b = new_project(user, "own"), new_project(user, "other")
    other = user.add_pdf(b, "hostel-rules.pdf", make_pdf(HOSTEL_RULES)).json()["id"]
    r = user.post("/graphs", json={"project_id": a, "name": "x", "graph": agent_graph({"s1": other})})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "source_not_in_project"
    r = user.post("/pipeline/run?stream=false", json={"question": "hi", "project_id": a, "graph": agent_graph({"s1": other})})
    assert r.status_code == 422


def test_upload_checks_and_listing(user):
    p = new_project(user, "uploads")
    assert user.post(f"/projects/{p}/sources/file", files={"file": ("x.txt", b"", "text/plain")}).status_code == 400
    assert user.post(f"/projects/{p}/sources/file", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    assert user.post(f"/projects/{p}/sources/file", files={"file": ("x.exe", b"MZ..", "application/octet-stream")}).status_code == 400
    user.add_pdf(p, "refund-policy.pdf", make_pdf(REFUND_POLICY))
    listed = user.get(f"/projects/{p}/sources").json()
    assert [(s["title"], s["kind"], s["status"]) for s in listed] == [("refund-policy.pdf", "pdf", "ready")]
    assert listed[0]["size_bytes"] > 0 and listed[0]["content_hash"] and listed[0]["chunk_count"] >= 1


def test_docs_url_is_ingested_with_title_and_link(user, page_server):
    r = user.post(f"/projects/{user.project}/sources/url", json={"url": f"{page_server}/moved"})  # follows the redirect
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "url" and body["title"] == "Clinic FAQ" and body["url"].endswith("/faq")
    from app.rag import store
    found = store.search(vector_for("clinic timings"), [f"src{body['id']}"], 3, f"project:{user.project}")
    assert found[0].origin.endswith("/faq") and "8 AM to 8 PM" in found[0].content and "menu" not in found[0].content


def test_pdf_link_is_ingested_as_pdf(user, page_server):
    r = user.post(f"/projects/{user.project}/sources/url", json={"url": f"{page_server}/rules.pdf"})
    assert r.status_code == 201 and r.json()["chunk_count"] >= 1


def test_internal_address_is_refused_and_recorded_as_failed(user, monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    for url in ["http://127.0.0.1:8000/health", "http://ollama:11434/api/tags", "http://169.254.169.254/latest/meta-data"]:
        r = user.post(f"/projects/{user.project}/sources/url", json={"url": url})
        assert r.status_code in (400, 422), (url, r.status_code)
    assert user.post(f"/projects/{user.project}/sources/url", json={"url": "file:///etc/passwd"}).status_code == 400
    failed = [s for s in user.get(f"/projects/{user.project}/sources").json() if s["status"] == "failed"]
    assert failed and all(s["chunk_count"] == 0 and s["error"] for s in failed)  # never shown as indexed


def test_github_repo_is_ingested_per_file(user, monkeypatch):
    from test_sources_units import make_zip
    from app.rag import fetch, repo
    data = make_zip({"README.md": b"# Readme\nThis readme explains the demo.", "src/main.py": b"print('readme')"})
    monkeypatch.setattr(repo, "fetch", lambda url, max_bytes, headers=None: fetch.Fetched(url, "application/zip", data))
    r = user.post(f"/projects/{user.project}/sources/repo", json={"url": "https://github.com/octocat/demo"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "octocat/demo@abc1234" and body["revision"] == "abc1234" and body["chunk_count"] == 2
    from app.rag import store
    found = store.search(vector_for("readme"), [f"src{body['id']}"], 5, f"project:{user.project}")
    assert {c.file_name for c in found} == {"README.md", "src/main.py"}
    assert all(c.content.startswith("File: ") and c.origin.startswith("https://github.com/octocat/demo/blob/") for c in found)


def test_bad_repo_link_is_a_clear_error(user):
    r = user.post(f"/projects/{user.project}/sources/repo", json={"url": "https://example.com/not-github"})
    assert r.status_code == 400 and "GitHub repository" in r.json()["detail"]


# ---------- agents: saved and unsaved (the builder's test run) ----------
def test_unsaved_canvas_graph_runs_against_its_agents_project(user):
    p = new_project(user, "canvas")
    sid = user.add_pdf(p, "hostel-rules.pdf", make_pdf(HOSTEL_RULES)).json()["id"]
    gid = user.post("/graphs", json={"project_id": p, "name": "canvas", "graph": agent_graph({"s1": sid})}).json()["id"]
    edited = user.get(f"/graphs/{gid}").json()
    edited = {"nodes": edited["nodes"], "edges": edited["edges"]}
    edited["nodes"][3]["config"]["language"] = "hi"  # an unsaved edit on the canvas
    r = user.post("/pipeline/run?stream=false", json={"question": "hostel gate", "graph": edited, "graph_id": gid})
    agent = next(e for e in r.json()["events"] if e.get("stage") == "agent" and e.get("status") == "done")
    assert agent["detail"]["retrieval"]["scope"] == f"project:{p}"
    assert {c["file"] for c in agent["detail"]["retrieval"]["candidates"]} == {"hostel-rules.pdf"}
    assert r.json()["events"][-1]["language"] == "hi"  # the edited graph ran, not the saved one


def test_run_with_unknown_graph_id_is_404(user, default_graph):
    r = user.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph, "graph_id": 999999})
    assert r.status_code == 404


def test_a_source_node_without_ready_knowledge_cannot_run(user):
    r = user.post("/pipeline/run?stream=false", json={"question": "hi", "project_id": user.project,
                                                       "graph": agent_graph({"s1": 999999})})
    assert r.status_code == 422
    g = agent_graph()
    g["nodes"].append({"id": "s9", "type": "source_doc", "config": {}})
    g["edges"].append({"from": "s9", "to": "n4"})
    r = user.post("/pipeline/run?stream=false", json={"question": "hi", "project_id": user.project, "graph": g})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "source_not_selected"


def test_update_saved_graph_keeps_id_links_and_positions(user):
    p = new_project(user, "layout")
    sid = user.add_pdf(p, "refund-policy.pdf", make_pdf(REFUND_POLICY)).json()["id"]
    gid = user.post("/graphs", json={"project_id": p, "name": "layout", "graph": agent_graph({"s1": sid})}).json()["id"]
    g = user.get(f"/graphs/{gid}").json()
    g = {"nodes": g["nodes"], "edges": g["edges"]}
    for i, n in enumerate(g["nodes"]):
        n["position"] = {"x": 100 * i, "y": 50}
    r = user.put(f"/graphs/{gid}", json={"name": "layout v2", "graph": g})
    assert r.status_code == 200 and r.json()["name"] == "layout v2"
    again = user.get(f"/graphs/{gid}").json()
    assert [n["position"] for n in again["nodes"]] == [{"x": 100 * i, "y": 50} for i in range(len(g["nodes"]))]
    assert next(x for x in user.get(f"/graphs?project_id={p}").json() if x["id"] == gid)["sources"] == 1
    assert user.get(f"/sources/{sid}").json()["agents"][0]["id"] == gid


def test_update_refuses_an_invalid_graph_and_unknown_id(user, full_graph):
    p = new_project(user, "strict")
    sid = user.add_pdf(p, "refund-policy.pdf", make_pdf(REFUND_POLICY)).json()["id"]
    gid = user.post("/graphs", json={"project_id": p, "name": "strict", "graph": agent_graph({"s1": sid})}).json()["id"]
    g = user.get(f"/graphs/{gid}").json()
    g = {"nodes": g["nodes"], "edges": [e for e in g["edges"] if e["from"] != "s1"]}  # source no longer connected
    r = user.put(f"/graphs/{gid}", json={"name": "broken", "graph": g})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "not_connected"
    assert user.put("/graphs/999999", json={"name": "x", "graph": full_graph}).status_code == 404


def test_deleting_an_agent_keeps_its_sources_and_deleting_a_source_unlinks_agents(user):
    p = new_project(user, "lifecycle")
    sid = user.add_pdf(p, "refund-policy.pdf", make_pdf(REFUND_POLICY)).json()["id"]
    g1 = user.post("/graphs", json={"project_id": p, "name": "one", "graph": agent_graph({"s1": sid})}).json()["id"]
    g2 = user.post("/graphs", json={"project_id": p, "name": "two", "graph": agent_graph({"s1": sid})}).json()["id"]
    assert user.delete(f"/graphs/{g1}").status_code == 200
    assert user.get(f"/sources/{sid}").json()["status"] == "ready"  # still there for agent two
    out = user.delete(f"/sources/{sid}").json()
    assert out["unlinked_agents"] == ["two"]
    from app.db import get_conn
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM chunks WHERE source_ref = %s", (sid,)).fetchone()[0] == 0
    r = user.post("/pipeline/run?stream=false", json={"question": "refund", "graph_id": g2})
    assert r.status_code == 422  # the deleted source can never be searched again
