"""Phase 7 tools: the safe calculator, the tool plan, the runner's safety rules, tool results reaching the
model as citable sources, and the real MCP server (official SDK) with the api's MCP client."""

import dataclasses
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.models import gemini
from app.pipeline.engine import run_pipeline
from app.pipeline.graph import Graph, Node
from app.pipeline.stages import agent
from app.pipeline.validate import validate_graph
from app.tools import calculator, mcp_client, planner, runner
from app.tools.runner import ToolCall
from app.rag.store import Chunk

ROOT = Path(__file__).resolve().parent.parent


# ---------- calculator ----------
@pytest.mark.parametrize("expression,result", [
    ("1200 * 0.8", 960), ("1,200 x 80%", 960), ("18% of 4500", 810), ("0.1 + 0.2", 0.3),
    ("(300 + 3500) / 2", 1900), ("2 ** 10", 1024), ("round(10 / 3, 2)", 3.33), ("₹300 * 2", 600),
])
def test_calculator(expression, result):
    assert calculator.evaluate(expression)["result"] == result


@pytest.mark.parametrize("bad", ['__import__("os").system("x")', "a + 1", "9 ** 9999", "1 / 0", '"x" * 3', "",
                                 'open("f")', "(1).real", "[1, 2]", "lambda: 1", "1" * 300])
def test_calculator_refuses_anything_but_arithmetic(bad):
    with pytest.raises(calculator.CalculatorError):
        calculator.evaluate(bad)


# ---------- the tool plan ----------
OFFERED = [{"node_id": "t1", "tool": "calculator", "args": {"expression": "string"}, "description": "math"},
           {"node_id": "t2", "tool": "get_order", "args": {}, "description": "order"}]


def test_plan_is_read_from_json_even_in_a_code_block():
    calls, problem = planner.parse('```json\n{"calls": [{"node_id": "t1", "tool": "calculator", "args": {"expression": "2*3"}}]}\n```', OFFERED)
    assert [(c.node_id, c.tool, c.args) for c in calls] == [("t1", "calculator", {"expression": "2*3"})] and problem is None


def test_plan_cannot_reach_tools_that_were_not_offered():
    text = json.dumps({"calls": [{"node_id": "t9", "tool": "calculator", "args": {}},
                                 {"node_id": "t2", "tool": "cancel_order", "args": {}},
                                 {"node_id": "t2", "tool": "get_order", "args": {"order_id": "ORD-1"}}]})
    calls, problem = planner.parse(text, OFFERED)
    assert [c.tool for c in calls] == ["get_order"] and "t9.calculator" in problem and "t2.cancel_order" in problem


def test_unreadable_plan_means_no_tools():
    assert planner.parse("I would use the calculator.", OFFERED) == ([], "The model did not return a tool plan.")
    assert planner.parse("{not json}", OFFERED)[0] == []


def test_plan_is_limited_to_three_calls():
    text = json.dumps({"calls": [{"node_id": "t1", "tool": "calculator", "args": {"expression": "1"}}] * 5})
    assert len(planner.parse(text, OFFERED)[0]) == planner.MAX_CALLS


# ---------- the runner's safety rules ----------
CALC = Node(id="t1", type="tool_calculator", config={})
MCP = Node(id="t2", type="tool_mcp", config={"server": "orders-db", "mode": "read_only"})


def test_unconnected_tool_never_runs():
    out = runner.run(ToolCall("t1", "calculator", {"expression": "1+1"}), {})
    assert out["ok"] is False and "not connected" in out["error"]


def test_calculator_error_is_a_result_not_a_crash():
    out = runner.run(ToolCall("t1", "calculator", {"expression": "1/0"}), {"t1": CALC})
    assert out["ok"] is False and "Division by zero" in out["error"] and out["ms"] >= 0


def test_read_only_mcp_tool_runs():
    out = runner.run(ToolCall("t2", "get_order", {"order_id": "ORD-1002"}), {"t2": MCP})
    assert out["ok"] is True and "960" in out["output"]


def test_mcp_tool_that_is_not_read_only_is_refused():
    out = runner.run(ToolCall("t2", "cancel_order", {"order_id": "ORD-1002"}), {"t2": MCP})
    assert out["ok"] is False and "not read-only" in out["error"]


def test_mcp_tool_list_hides_write_tools():
    names = [t["tool"] for t in runner.describe([MCP])]
    assert names == ["get_order"]


def test_unknown_mcp_server_is_refused_by_validation(full_graph):
    full_graph["nodes"][-1]["config"]["server"] = "http://evil.example.com/mcp"
    errs = validate_graph(Graph.model_validate(full_graph))
    assert [(e["code"], e["node_id"]) for e in errs] == [("unknown_mcp_server", "t2")]


def test_mcp_client_only_reaches_allowlisted_servers():
    with pytest.raises(mcp_client.MCPError, match="Unknown MCP server"):
        mcp_client.server_url("http://169.254.169.254/")


# ---------- tools inside the pipeline ----------
REFUND = Chunk(7, "s1", "refund-policy.pdf", 0, 1, "If a lab test is cancelled before the sample is collected, 80 percent "
               "of the test amount is refunded.", 0.7)


def scripted_model(monkeypatch, plan: str, answer: str):
    """The planner call gets `plan`, the answer call gets `answer`; records the prompts it saw."""
    seen = []

    def stream(system, prompt, **kwargs):
        seen.append(prompt)
        text = plan if system == planner.PLANNER_SYSTEM else answer
        yield {"text": text}
        yield {"usage": {"input_tokens": 100, "output_tokens": 10}}
    monkeypatch.setattr(gemini, "stream", stream)
    return seen


def tool_graph(full_graph, keep=("s1", "t1")):
    ids = {"n1", "n2", "n3", "n4", "n5", *keep}
    return Graph.model_validate({"nodes": [n for n in full_graph["nodes"] if n["id"] in ids],
                                 "edges": [e for e in full_graph["edges"] if e["from"] in ids]})


QUESTION = "My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?"


def test_tool_result_reaches_the_model_and_is_cited(monkeypatch, full_graph):
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline", broad=False: ([REFUND], [REFUND]))
    seen = scripted_model(monkeypatch, '{"calls": [{"node_id": "t1", "tool": "calculator", "args": {"expression": "1200 * 80%"}}]}',
                          "You get 960 rupees back [2], because 80 percent is refunded [1].")
    events = list(run_pipeline(tool_graph(full_graph), QUESTION))
    kinds = [e["type"] for e in events]
    assert "tool_call" in kinds and "tool_result" in kinds
    result_event = next(e for e in events if e["type"] == "tool_result")
    assert result_event["node_id"] == "t1" and result_event["ok"] and result_event["output"] == "1200 * 80% = 960"
    assert "1200 * 80% = 960" in seen[-1]  # the tool output was in the answer prompt
    final = events[-1]
    assert final["supported"] is True and final["tool_calls"][0]["output"] == "1200 * 80% = 960"
    assert {c["source_id"] for c in final["citations"]} == {"s1", "t1"}
    assert final["usage"]["input_tokens"] == 200  # tool plan + answer, added up into one answer


def test_unconnected_tool_is_never_offered_to_the_model(monkeypatch, full_graph):
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline", broad=False: ([REFUND], [REFUND]))
    seen = scripted_model(monkeypatch, '{"calls": [{"node_id": "t1", "tool": "calculator", "args": {"expression": "1+1"}}]}', "x [1].")
    graph = tool_graph(full_graph, keep=("s1",))  # no tool drawn into the agent
    events = list(run_pipeline(graph, QUESTION))
    assert not any(e["type"] == "tool_call" for e in events)
    assert all("calculator" not in p for p in seen)


def test_failing_tool_is_reported_and_the_answer_continues(monkeypatch, full_graph):
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline", broad=False: ([REFUND], [REFUND]))
    scripted_model(monkeypatch, '{"calls": [{"node_id": "t1", "tool": "calculator", "args": {"expression": "1200/0"}}]}',
                   "80 percent of the test amount is refunded [1].")
    events = list(run_pipeline(tool_graph(full_graph), QUESTION))
    failed = next(e for e in events if e["type"] == "tool_result")
    assert failed["ok"] is False and "Division by zero" in failed["error"]
    assert events[-1]["supported"] is True and events[-1]["tool_calls"][0]["ok"] is False


def test_order_question_uses_the_mcp_tool(monkeypatch, full_graph):
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline", broad=False: ([], []))
    scripted_model(monkeypatch, '{"calls": [{"node_id": "t2", "tool": "get_order", "args": {"order_id": "ORD-1002"}}]}',
                   "Order ORD-1002 gets a refund of 960 [1].")
    events = list(run_pipeline(tool_graph(full_graph, keep=("t2",)), "What is the refund for order ORD-1002?"))
    router = next(e for e in events if e.get("stage") == "router" and e.get("status") == "done")
    assert router["detail"]["route"] == "tool"
    assert events[-1]["supported"] and events[-1]["citations"][0]["source_id"] == "t2"


def test_local_only_tool_plan_uses_only_local_models(monkeypatch, full_graph):
    from app.models import ollama
    cloud = []
    monkeypatch.setattr(gemini, "stream", lambda s, p, **k: cloud.append(1) or iter(()))
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline", broad=False: ([REFUND], [REFUND]))

    def local(system, prompt, **kwargs):
        yield {"text": '{"calls": []}' if system == planner.PLANNER_SYSTEM else "80 percent is refunded [1]."}
        yield {"usage": {"input_tokens": 5, "output_tokens": 5}}
    monkeypatch.setattr(ollama, "stream", local)
    events = list(run_pipeline(tool_graph(full_graph), QUESTION, None, "local", None, "inline", True))
    assert cloud == [] and events[-1]["model"].startswith("local:") and events[-1]["local_only"] is True


# ---------- the real MCP server and the api's SDK client ----------
@pytest.fixture(scope="module")
def mcp_server():
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 8100)) == 0:
            pytest.skip("port 8100 is busy")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT / "mcp-runner",
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", 8100)) == 0:
                break
        time.sleep(0.2)
    yield "http://127.0.0.1:8100/mcp"
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def real_client(monkeypatch, mcp_server):
    import importlib
    real = importlib.reload(mcp_client)  # undo the offline stubs from conftest
    monkeypatch.setattr(real, "settings", dataclasses.replace(real.settings, mcp_url=mcp_server))
    return real


@pytest.mark.real_models
def test_real_server_marks_its_only_write_tool(real_client):
    tools = {t["name"]: t for t in real_client.list_tools("orders-db")}
    assert {"get_order", "find_orders", "order_totals", "cancel_order"} <= set(tools)
    # cancel_order is the one tool that changes data, so it is not read-only (the api asks for approval first)
    assert [name for name, t in tools.items() if not t["read_only"]] == ["cancel_order"]


@pytest.mark.real_models
def test_real_server_answers_an_order_lookup(real_client):
    out = json.loads(real_client.call_tool("orders-db", "get_order", {"order_id": "ORD-1002"}))
    assert out["found"] is True and out["refund"] == 960 and out["amount"] == 1200
    totals = json.loads(real_client.call_tool("orders-db", "order_totals", {}))
    assert totals["orders"] == 6


@pytest.mark.real_models
def test_real_server_rejects_unknown_tools(real_client):
    with pytest.raises(real_client.MCPError):
        real_client.call_tool("orders-db", "delete_everything", {})


def test_tools_endpoint_lists_read_only_mcp_tools():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes import tools
    app = FastAPI()
    app.include_router(tools.router)  # the real app adds the session check (test_tenancy_db.py)
    body = TestClient(app).get("/tools").json()
    assert body["mcp_servers"][0]["name"] == "orders-db" and body["mcp_servers"][0]["ok"]
    assert [t["name"] for t in body["mcp_servers"][0]["tools"]] == ["get_order", "cancel_order"]
    assert [t["read_only"] for t in body["mcp_servers"][0]["tools"]] == [True, False]



def test_unreadable_tool_plan_is_asked_for_once_more(monkeypatch, full_graph):
    """Phase 7.1: GPT-OSS sometimes returns an empty plan; one retry, then the tool runs."""
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline", broad=False: ([REFUND], [REFUND]))
    plans = iter(["", '{"calls": [{"node_id": "t1", "tool": "calculator", "args": {"expression": "1200 * 80%"}}]}'])

    def stream(system, prompt, **kwargs):
        yield {"text": next(plans) if system == planner.PLANNER_SYSTEM else "You get 960 rupees back [2]."}
        yield {"usage": {"input_tokens": 10, "output_tokens": 5}}
    monkeypatch.setattr(gemini, "stream", stream)
    events = list(run_pipeline(tool_graph(full_graph), QUESTION))
    final = events[-1]
    assert final["tool_calls"][0]["output"] == "1200 * 80% = 960"
    assert not any("tool plan" in w for w in final["warnings"])
    agent_done = next(e for e in events if e.get("stage") == "agent" and e.get("status") == "done")
    assert [s["step"] for s in agent_done["detail"]["generation"]["steps"]] == ["tool_plan", "tool_plan", "answer"]


def test_tool_plan_is_retried_only_once(monkeypatch, full_graph):
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline", broad=False: ([REFUND], [REFUND]))
    seen = scripted_model(monkeypatch, "no plan here", "80 percent of the test amount is refunded [1].")
    events = list(run_pipeline(tool_graph(full_graph), QUESTION))
    assert len(seen) == 3 and "The model did not return a tool plan." in events[-1]["warnings"]


def test_embedding_model_down_is_a_clear_error(monkeypatch, full_graph):
    """Phase 7.1: with Ollama stopped the question cannot be embedded; the run ends with a named error."""
    from app.rag.embeddings import EmbeddingError

    def down(q, ids, scope="inline", broad=False):
        raise EmbeddingError("Cannot reach Ollama at http://ollama:11434")
    monkeypatch.setattr(agent, "retrieve", down)
    events = list(run_pipeline(tool_graph(full_graph, keep=("s1",)), "What is the refund policy?"))
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "embedding_unavailable" and "Cannot reach Ollama" in err["message"]
