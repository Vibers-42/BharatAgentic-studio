"""The /pipeline HTTP endpoints: validation errors, the SSE stream, and the JSON mode.
Uses only the pipeline router, so it needs no database."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import pipeline


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(pipeline.router)
    return TestClient(app)


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
    with client.stream("POST", "/pipeline/run", json={"graph": full_graph, "question": "refund policy?"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.read().decode("utf-8"))
    stages = [e for e in events if e["type"] == "stage"]
    assert len(stages) == 10  # running + done for each of the 5 stages
    assert events[-1]["type"] == "result"


def test_run_stream_keeps_telugu_text(client, full_graph):
    with client.stream("POST", "/pipeline/run",
                       json={"graph": full_graph, "question": "రిఫండ్ పాలసీ ఏమిటి?"}) as r:
        events = parse_sse(r.read().decode("utf-8"))
    assert events[-1]["language"] == "te"


def test_run_json_mode(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"graph": default_graph, "question": "hi"})
    assert r.status_code == 200
    assert r.json()["events"][-1]["type"] == "result"


def test_run_rejects_invalid_graph_before_streaming(client, default_graph):
    default_graph["edges"].pop()  # remove agent -> output
    r = client.post("/pipeline/run", json={"graph": default_graph, "question": "refund policy?"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["message"] == "The pipeline graph is not valid."
    assert detail["errors"][0]["code"] == "main_line_gap"


def test_run_needs_a_graph(client, default_graph):
    # Since Phase 6, graph + graph_id together is allowed (canvas graph, saved graph's documents);
    # that case needs a database and is tested in test_sources_db.py.
    assert client.post("/pipeline/run", json={"question": "hi"}).status_code == 400


def test_malformed_graph_is_422(client):
    r = client.post("/pipeline/validate", json={"edges": []})
    assert r.status_code == 422


# ---------- local-only privacy option ----------
def test_local_only_refuses_a_cloud_model_before_running(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"graph": default_graph, "question": "hi", "model": "gemini",
                                                        "local_only": True})
    assert r.status_code == 400 and "Local-only mode is on" in r.json()["detail"]


def test_local_only_from_the_agent_node(client, default_graph):
    default_graph["nodes"][3]["config"].update({"model": "groq", "local_only": True})
    r = client.post("/pipeline/run?stream=false", json={"graph": default_graph, "question": "hi"})
    assert r.status_code == 400


def test_local_only_with_a_local_model_runs_and_is_marked(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"graph": default_graph, "question": "hi there", "model": "local",
                                                        "local_only": True})
    result = r.json()["events"][-1]
    assert r.status_code == 200 and result["local_only"] is True and result["model"].startswith("local:")
