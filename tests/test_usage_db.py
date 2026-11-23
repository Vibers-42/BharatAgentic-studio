"""Phase 4 against a real Postgres: users, one usage row per model answer (including fallbacks and
failures), and the usage API by user, model and day. Models are faked; skipped without TEST_DATABASE_URL."""

import os

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a Postgres with pgvector"),
              pytest.mark.real_usage]

if DB_URL:
    os.environ["DATABASE_URL"] = DB_URL


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        c.post("/users", json={"name": "p4_alice"})
        c.post("/users", json={"name": "p4_bob"})
        yield c
        from app.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM usage WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'p4_%')")
            conn.execute("DELETE FROM users WHERE name LIKE 'p4_%'")


def ask(client, default_graph, user, model=None, question="hello there"):
    body = {"question": question, "graph": default_graph, "user": user}
    if model:
        body["model"] = model
    r = client.post("/pipeline/run?stream=false", json=body)
    assert r.status_code == 200, r.text
    return r.json()["events"][-1]


def latest(client, user):
    return client.get(f"/usage/recent?user={user}&limit=1").json()[0]


def test_users_list_and_add(client):
    names = [u["name"] for u in client.get("/users").json()]
    assert {"demo", "p4_alice", "p4_bob"} <= set(names)
    assert client.post("/users", json={"name": "bad name!"}).status_code == 400
    again = client.post("/users", json={"name": "p4_alice"})  # adding twice is harmless
    assert again.status_code == 201


def test_unknown_user_and_model_are_refused(client, default_graph):
    r = client.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph, "user": "nobody"})
    assert r.status_code == 400 and "Unknown user" in r.json()["detail"]
    r = client.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph, "model": "gpt-9"})
    assert r.status_code == 400 and "Unknown model" in r.json()["detail"]


def test_answer_is_logged_with_tokens_cost_and_sources(client, default_graph):
    result = ask(client, default_graph, "p4_alice", "gemini")
    row = latest(client, "p4_alice")
    assert row["model"] == result["model"] and row["model_requested"] == "gemini" and row["status"] == "ok"
    assert (row["input_tokens"], row["output_tokens"], row["tokens_estimated"]) == (10, 2, False)
    assert row["cost_usd"] == pytest.approx(result["cost"]["usd"]) and row["cost_usd"] > 0
    assert row["cost_inr"] == pytest.approx(result["cost"]["inr"])
    assert row["sources"] == [] and row["language"] == "en" and row["response_ms"] >= 0


def test_local_answer_costs_nothing_but_records_compute(client, default_graph):
    result = ask(client, default_graph, "p4_alice", "local")
    row = latest(client, "p4_alice")
    assert result["cost"]["api_fee"] is False and row["cost_usd"] == 0
    assert row["local_compute_ms"] == result["response_ms"]


def test_unpriced_model_logs_null_cost(client, default_graph, monkeypatch):
    import dataclasses
    from app.models import groq
    # llama-3.3-70b-versatile has no published price in prices.json
    monkeypatch.setattr(groq, "settings", dataclasses.replace(groq.settings, groq_model="llama-3.3-70b-versatile"))
    ask(client, default_graph, "p4_bob", "groq")
    row = latest(client, "p4_bob")
    assert row["model"].startswith("groq:") and row["cost_usd"] is None


def test_fallback_is_logged(client, default_graph, monkeypatch):
    from app.models import gemini
    from app.models.base import QuotaError

    def limited(system, prompt, **kwargs):
        raise QuotaError("daily", "used up")
        yield  # pragma: no cover
    monkeypatch.setattr(gemini, "stream", limited)
    result = ask(client, default_graph, "p4_bob", "gemini")
    row = latest(client, "p4_bob")
    assert result["model"].startswith("local:") and row["fallback_from"] == "gemini" and row["status"] == "ok"


def test_total_failure_is_logged_as_error(client, default_graph, monkeypatch):
    from app.models import gemini, ollama
    from app.models.base import ProviderError

    def broken(system, prompt, **kwargs):
        raise ProviderError("down", code="network_error")
        yield  # pragma: no cover
    monkeypatch.setattr(gemini, "stream", broken)
    monkeypatch.setattr(ollama, "stream", broken)
    r = client.post("/pipeline/run?stream=false", json={"question": "hello there", "graph": default_graph,
                                                         "user": "p4_bob"})
    assert r.json()["events"][-1]["code"] == "all_providers_failed"
    row = latest(client, "p4_bob")
    assert row["status"] == "error" and row["error_code"] == "all_providers_failed"


def test_usage_totals_by_user_model_and_day(client):
    alice = client.get("/usage/totals?user=p4_alice").json()
    assert alice["requests"] == 2 and alice["cost_usd"] > 0 and alice["local_compute_ms"] >= 0
    bob = client.get("/usage/totals?user=p4_bob").json()
    assert bob["requests"] == 3 and bob["errors"] == 1 and bob["fallbacks"] == 2 and bob["unpriced_requests"] == 1

    users = {u["user"]: u for u in client.get("/usage/by-user").json()}
    assert users["p4_alice"]["requests"] == 2 and users["p4_bob"]["requests"] == 3

    models = {m["model"]: m for m in client.get("/usage/by-model?user=p4_alice").json()}
    assert {k.split(":")[0] for k in models} == {"gemini", "local"}

    days = client.get("/usage/by-day?user=p4_alice&days=7").json()
    assert len(days) == 1 and days[0]["requests"] == 2


def test_models_endpoint_lists_all_four(client):
    body = client.get("/models").json()
    keys = {m["key"]: m for m in body["models"]}
    assert set(keys) == {"gemini", "groq", "local", "local_small"}
    for local in ("local", "local_small"):
        assert keys[local]["local"] is True and keys[local]["input_per_million_usd"] == 0
    assert keys["groq"]["model_id"] == "openai/gpt-oss-120b" and keys["groq"]["priced"] is True
    assert body["prices_checked_on"] and body["fallback"] == "local" and body["local_only"] is False
