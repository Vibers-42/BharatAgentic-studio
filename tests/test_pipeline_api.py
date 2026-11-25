"""The /pipeline HTTP endpoints: validation errors, the SSE stream, and the JSON mode.
Uses only the pipeline router and no database: the signed-in user and the project's access record are test
fixtures here (dependency override in this test app only). The real checks are tested against Postgres in
test_tenancy_db.py."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import pipeline


@pytest.fixture
def client(monkeypatch):
    from types import SimpleNamespace
    from app.auth import User, current_user
    app = FastAPI()
    app.include_router(pipeline.router)
    app.dependency_overrides[current_user] = lambda: User(1, "tester@example.test", "Tester", 1, "csrf")
    access = SimpleNamespace(org_id=1, local_only=False, can_contribute=True, can_manage=True)
    monkeypatch.setattr(pipeline, "require_project", lambda user, pid, need="view": access)
    monkeypatch.setattr(pipeline, "project_access", lambda user_id, pid: access)
    monkeypatch.setattr(pipeline, "check_sources", lambda graph, pid: [])
    monkeypatch.setattr(pipeline, "_source_map", lambda graph, pid: {n.id: n.id for n in graph.nodes if n.type.startswith("source")})
    monkeypatch.setattr(pipeline.budgets, "check", lambda pid, conversation_id=None: (None, []))  # no spend limit
    return TestClient(app)


def test_spend_limit_refuses_cloud_and_keeps_local_runs_local(client, default_graph, monkeypatch):
    monkeypatch.setattr(pipeline.budgets, "check", lambda pid, conversation_id=None: ("Spend limit reached: test", []))
    r = client.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph, "project_id": 1, "model": "groq"})
    assert r.status_code == 402 and "Spend limit reached" in r.json()["detail"]
    seen = {}
    real = pipeline.run_pipeline

    def spy(*args, **kwargs):
        seen["local_only"] = args[6] if len(args) > 6 else kwargs.get("local_only")
        return real(*args, **kwargs)
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    r = client.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph, "project_id": 1, "model": "local"})
    assert r.status_code == 200 and seen["local_only"] is True  # a local model may answer, never a paid fallback


def test_budget_warning_is_the_first_event(client, default_graph, monkeypatch):
    monkeypatch.setattr(pipeline.budgets, "check", lambda pid, conversation_id=None: (None, ["85% of the limit is used"]))
    events = client.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph, "project_id": 1}).json()["events"]
    assert events[0] == {"type": "budget", "stage": "input", "node_id": None, "level": "warning", "message": "85% of the limit is used"}


def test_approval_decision_endpoint_only_for_the_asker(client):
    from app.tools import approvals
    mine = approvals.request(1, "orders-db.cancel_order", {"order_id": "ORD-1003"}, "t2")
    theirs = approvals.request(2, "orders-db.cancel_order", {"order_id": "ORD-1004"}, "t2")
    assert client.post(f"/pipeline/approvals/{theirs.id}", json={"approve": True}).status_code == 404
    r = client.post(f"/pipeline/approvals/{mine.id}", json={"approve": False})
    assert r.status_code == 200 and r.json()["decision"] == "denied"
    assert client.post(f"/pipeline/approvals/{mine.id}", json={"approve": True}).status_code == 404


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        data = json.loads(lines["data"])
        assert lines["event"] == data["type"]
        events.append(data)
    return events


def test_default_graph(client, default_graph):
    assert client.get("/pipeline/default").json() == default_graph


def test_validate_ok(client, full_graph):
    assert client.post("/pipeline/validate", json=full_graph).json() == {"valid": True, "errors": []}


def test_validate_reports_errors(client, full_graph):
    full_graph["edges"] = [e for e in full_graph["edges"] if e["from"] != "s1"]
    body = client.post("/pipeline/validate", json=full_graph).json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "not_connected"


def test_run_streams_sse(client, full_graph):
    with client.stream("POST", "/pipeline/run", json={"project_id": 1, "graph": full_graph, "question": "refund policy?"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.read().decode("utf-8"))
    stages = [e for e in events if e["type"] == "stage"]
    assert len(stages) == 10  # running + done for each of the 5 stages
    assert events[-1]["type"] == "result"


def test_run_stream_keeps_telugu_text(client, full_graph):
    with client.stream("POST", "/pipeline/run",
                       json={"project_id": 1, "graph": full_graph, "question": "రిఫండ్ పాలసీ ఏమిటి?"}) as r:
        events = parse_sse(r.read().decode("utf-8"))
    assert events[-1]["language"] == "te"


def test_run_json_mode(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"project_id": 1, "graph": default_graph, "question": "hi"})
    assert r.status_code == 200
    assert r.json()["events"][-1]["type"] == "result"


def test_run_rejects_invalid_graph_before_streaming(client, default_graph):
    default_graph["edges"].pop()  # remove agent -> output
    r = client.post("/pipeline/run", json={"project_id": 1, "graph": default_graph, "question": "refund policy?"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["message"] == "The pipeline graph is not valid."
    assert detail["errors"][0]["code"] == "main_line_gap"


def test_run_needs_an_agent_a_project_or_a_conversation(client, default_graph):
    # Phase 8: a graph alone no longer runs; it must belong to a project the user can use (see test_tenancy_db.py).
    assert client.post("/pipeline/run", json={"question": "hi"}).status_code == 400
    assert client.post("/pipeline/run", json={"question": "hi", "graph": default_graph}).status_code == 400


def test_malformed_graph_is_422(client):
    r = client.post("/pipeline/validate", json={"edges": []})
    assert r.status_code == 422


# ---------- local-only privacy option ----------
def test_local_only_refuses_a_cloud_model_before_running(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"project_id": 1, "graph": default_graph, "question": "hi", "model": "gemini",
                                                        "local_only": True})
    assert r.status_code == 400 and "local-only mode is on" in r.json()["detail"]


def test_local_only_from_the_agent_node(client, default_graph):
    default_graph["nodes"][3]["config"].update({"model": "groq", "local_only": True})
    r = client.post("/pipeline/run?stream=false", json={"project_id": 1, "graph": default_graph, "question": "hi"})
    assert r.status_code == 400


def test_local_only_with_a_local_model_runs_and_is_marked(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"project_id": 1, "graph": default_graph, "question": "hi there", "model": "local",
                                                        "local_only": True})
    result = r.json()["events"][-1]
    assert r.status_code == 200 and result["local_only"] is True and result["model"].startswith("local:")
