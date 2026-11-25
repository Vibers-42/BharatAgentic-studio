"""Human approval for tools that change data: such a tool never runs unless the person who asked approves it,
in the live stream; a denial, a timeout or a run without a live stream changes nothing."""

import copy

import pytest

from app.pipeline import engine
from app.pipeline.graph import Graph
from app.pipeline.stages import agent
from app.tools import approvals, mcp_client
from app.tools.runner import ToolCall

USER = 42


@pytest.fixture
def write_graph(full_graph):
    g = copy.deepcopy(full_graph)
    for n in g["nodes"]:
        if n["type"] == "tool_mcp":
            n["config"] = {"server": "orders-db", "mode": "read_write", "allow_write": True}
    return Graph.model_validate(g)


@pytest.fixture
def plan_cancel(monkeypatch):
    """The planner asks for cancel_order on the MCP node; the MCP call is recorded."""
    monkeypatch.setattr(agent.planner, "parse", lambda text, tools: ([ToolCall("t2", "cancel_order", {"order_id": "ORD-1003"})], None))
    called = []
    monkeypatch.setattr(mcp_client, "call_tool", lambda server, tool, args: called.append((tool, args)) or '{"changed": true}')
    return called


def run_until_approval(graph, can_approve=True):
    events = engine.run_pipeline(graph, "Please cancel order ORD-1003", user_id=USER, can_approve=can_approve)
    seen = []
    for ev in events:
        seen.append(ev)
        if ev["type"] == "approval_required":
            return events, seen, ev
    return events, seen, None


def test_approved_action_runs_once_the_user_says_yes(write_graph, plan_cancel):
    events, seen, ask = run_until_approval(write_graph)
    assert ask["tool"] == "orders-db.cancel_order" and ask["args"] == {"order_id": "ORD-1003"}
    assert plan_cancel == []  # nothing has run while waiting
    assert approvals.decide(ask["approval_id"], USER, True) == "approved"
    rest = list(events)
    assert plan_cancel == [("cancel_order", {"order_id": "ORD-1003"})]
    assert any(e["type"] == "approval_result" and e["decision"] == "approved" for e in rest)
    assert any(e["type"] == "tool_result" and e["ok"] for e in rest)


def test_denied_action_never_runs_and_the_answer_is_told(write_graph, plan_cancel):
    events, _, ask = run_until_approval(write_graph)
    assert approvals.decide(ask["approval_id"], USER, False) == "denied"
    rest = list(events)
    assert plan_cancel == []
    result = next(e for e in rest if e["type"] == "tool_result")
    assert result["ok"] is False and result["denied"] is True and "Nothing was changed" in result["error"]


def test_silence_is_a_no(write_graph, plan_cancel, monkeypatch):
    monkeypatch.setattr(approvals, "TIMEOUT_SECONDS", 0.01)
    rest = list(engine.run_pipeline(write_graph, "Please cancel order ORD-1003", user_id=USER, can_approve=True))
    assert plan_cancel == []
    assert next(e for e in rest if e["type"] == "approval_result")["decision"] == "timeout"


def test_without_a_live_stream_the_action_is_refused_without_asking(write_graph, plan_cancel):
    rest = list(engine.run_pipeline(write_graph, "Please cancel order ORD-1003", user_id=USER, can_approve=False))
    assert plan_cancel == [] and not any(e["type"] == "approval_required" for e in rest)
    assert "live chat" in next(e for e in rest if e["type"] == "tool_result")["error"]


def test_only_the_asker_can_decide_and_only_once():
    pending = approvals.request(USER, "orders-db.cancel_order", {"order_id": "ORD-1"}, "t2")
    assert approvals.decide(pending.id, USER + 1, True) is None      # someone else
    assert approvals.decide("made-up-id", USER, True) is None       # unknown id
    assert approvals.decide(pending.id, USER, True) == "approved"
    assert approvals.decide(pending.id, USER, False) is None        # already decided
    assert approvals.wait(pending, timeout=0) == "approved"


def test_read_only_tools_never_ask(full_graph, monkeypatch):
    monkeypatch.setattr(agent.planner, "parse", lambda text, tools: ([ToolCall("t2", "get_order", {"order_id": "ORD-1002"})], None))
    rest = list(engine.run_pipeline(Graph.model_validate(full_graph), "status of order ORD-1002", user_id=USER, can_approve=True))
    assert not any(e["type"] == "approval_required" for e in rest)
    assert any(e["type"] == "tool_result" and e["ok"] for e in rest)


def test_designer_orders_actions_allows_writes_only_on_purpose():
    import json
    from app.pipeline import designer
    reply = {"name": "Orders desk", "language": "en", "sources": [], "tools": ["orders", "orders_actions"]}
    design = designer.parse_design(json.dumps(reply), {})
    graph = designer.compile_design(design, "groq")
    mcp = [n for n in graph.nodes if n.type == "tool_mcp"]
    assert len(mcp) == 1 and mcp[0].config == {"server": "orders-db", "mode": "read_write", "allow_write": True}
    assert design.sources == []  # an orders desk needs no document


def test_an_empty_tool_plan_is_asked_for_once_more(full_graph, monkeypatch):
    plans = iter([([], None), ([ToolCall("t2", "get_order", {"order_id": "ORD-1002"})], None)])
    monkeypatch.setattr(agent.planner, "parse", lambda text, tools: next(plans))
    rest = list(engine.run_pipeline(Graph.model_validate(full_graph), "status of order ORD-1002", user_id=USER))
    assert any(e["type"] == "tool_result" and e["ok"] for e in rest)
