"""Phase 4 usage, under Phase 8 accounts, against a real Postgres: one usage row per model answer (including
fallbacks and failures), attributed to the SIGNED-IN user (a user name in the request body is ignored), and the
usage API by user, model and day, where each person sees only their own rows. Models are faked; skipped without
TEST_DATABASE_URL."""

import os

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a Postgres with pgvector"),
              pytest.mark.real_usage]

if DB_URL:
    os.environ["DATABASE_URL"] = DB_URL


@pytest.fixture(scope="module")
def app_client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def alice(app_client):
    from accounts import Account
    from app.main import app
    return Account(app, "p4_alice")


@pytest.fixture(scope="module")
def bob(app_client):
    from accounts import Account
    from app.main import app
    return Account(app, "p4_bob")


def ask(acct, default_graph, model=None, question="hello there", **extra):
    body = {"question": question, "graph": default_graph, "project_id": acct.project, **extra}
    if model:
        body["model"] = model
    r = acct.post("/pipeline/run?stream=false", json=body)
    assert r.status_code == 200, r.text
    return r.json()["events"][-1]


def latest(acct):
    return acct.get("/usage/recent?limit=1").json()[0]


def test_usage_goes_to_the_signed_in_user_not_the_body(alice, bob, default_graph):
    ask(alice, default_graph, "gemini", user="p4_bob")  # Phase 4 trusted this field; now it is ignored
    assert latest(alice)["user"] == alice.name
    assert bob.get("/usage/totals").json()["requests"] == 0


def test_unknown_model_is_refused(alice, default_graph):
    r = alice.post("/pipeline/run?stream=false", json={"question": "hi", "graph": default_graph,
                                                        "project_id": alice.project, "model": "gpt-9"})
    assert r.status_code == 400 and "Unknown model" in r.json()["detail"]


def test_answer_is_logged_with_tokens_cost_and_sources(alice, default_graph):
    result = ask(alice, default_graph, "gemini")
    row = latest(alice)
    assert row["model"] == result["model"] and row["model_requested"] == "gemini" and row["status"] == "ok"
    assert (row["input_tokens"], row["output_tokens"], row["tokens_estimated"]) == (10, 2, False)
    assert row["cost_usd"] == pytest.approx(result["cost"]["usd"]) and row["cost_usd"] > 0
    assert row["cost_inr"] == pytest.approx(result["cost"]["inr"])
    assert row["sources"] == [] and row["language"] == "en" and row["response_ms"] >= 0
    assert row["project"] == "My first project"


def test_local_answer_costs_nothing_but_records_compute(alice, default_graph):
    result = ask(alice, default_graph, "local")
    row = latest(alice)
    assert result["cost"]["api_fee"] is False and row["cost_usd"] == 0
    assert row["local_compute_ms"] == result["response_ms"]


def test_unpriced_model_logs_null_cost(bob, default_graph, monkeypatch):
    import dataclasses
    from app.models import groq
    # llama-3.3-70b-versatile has no published price in prices.json
    monkeypatch.setattr(groq, "settings", dataclasses.replace(groq.settings, groq_model="llama-3.3-70b-versatile"))
    ask(bob, default_graph, "groq")
    row = latest(bob)
    assert row["model"].startswith("groq:") and row["cost_usd"] is None


def test_fallback_is_logged(bob, default_graph, monkeypatch):
    from app.models import gemini
    from app.models.base import QuotaError

    def limited(system, prompt, **kwargs):
        raise QuotaError("daily", "used up")
        yield  # pragma: no cover
    monkeypatch.setattr(gemini, "stream", limited)
    result = ask(bob, default_graph, "gemini")
    row = latest(bob)
    assert result["model"].startswith("local:") and row["fallback_from"] == "gemini" and row["status"] == "ok"
    assert result["fallback"]["from"] == "gemini"  # the answer reports the model that really answered


def test_total_failure_is_logged_as_error(bob, default_graph, monkeypatch):
    from app.models import gemini, ollama
    from app.models.base import ProviderError

    def broken(system, prompt, **kwargs):
        raise ProviderError("down", code="network_error")
        yield  # pragma: no cover
    monkeypatch.setattr(gemini, "stream", broken)
    monkeypatch.setattr(ollama, "stream", broken)
    r = bob.post("/pipeline/run?stream=false", json={"question": "hello there", "graph": default_graph,
                                                      "project_id": bob.project})
    assert r.json()["events"][-1]["code"] == "all_providers_failed"
    row = latest(bob)
    assert row["status"] == "error" and row["error_code"] == "all_providers_failed"


def test_each_person_sees_only_their_own_usage(alice, bob):
    a = alice.get("/usage/totals").json()
    assert a["requests"] == 3 and a["cost_usd"] > 0 and a["local_compute_ms"] >= 0 and a["everyone"] is False
    b = bob.get("/usage/totals").json()
    assert b["requests"] == 3 and b["errors"] == 1 and b["fallbacks"] == 2 and b["unpriced_requests"] == 1
    assert [u["user"] for u in alice.get("/usage/by-user").json()] == [alice.name]
    models = {m["model"]: m for m in alice.get("/usage/by-model").json()}
    assert {k.split(":")[0] for k in models} == {"gemini", "local"}
    days = alice.get("/usage/by-day?days=7").json()
    assert len(days) == 1 and days[0]["requests"] == 3
    assert bob.get(f"/usage/totals?project_id={alice.project}").status_code == 404
    assert bob.get(f"/usage/totals?org_id={alice.personal}").status_code == 404


def test_usage_filters_by_model_and_days(alice):
    only_local = alice.get("/usage/totals?model=local").json()
    assert only_local["requests"] == 1 and only_local["cost_usd"] == 0
    assert alice.get("/usage/totals?model=groq").json()["requests"] == 0
    assert alice.get("/usage/totals?days=1").json()["requests"] == 3
    assert {r["model"].split(":")[0] for r in alice.get("/usage/recent?model=gemini").json()} == {"gemini"}
    assert alice.get("/usage/totals?model=DROP%20TABLE").status_code == 422
    assert alice.c.__class__(alice.c.app).get("/usage/totals").status_code == 401  # no session, no usage


def test_models_endpoint_lists_all_four(alice):
    body = alice.get("/models").json()
    keys = {m["key"]: m for m in body["models"]}
    assert set(keys) == {"gemini", "groq", "local", "local_small"}
    for local in ("local", "local_small"):
        assert keys[local]["local"] is True and keys[local]["input_per_million_usd"] == 0
    assert keys["groq"]["model_id"] == "openai/gpt-oss-120b" and keys["groq"]["priced"] is True
    assert body["prices_checked_on"] and body["fallback"] == "local" and body["local_only"] is False
