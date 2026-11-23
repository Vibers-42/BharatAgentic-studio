"""Every validation rule from the handoff: each broken graph must return its own clear error."""

from app.pipeline.graph import Graph
from app.pipeline.validate import validate_graph


def errors_for(data: dict) -> list[dict]:
    return validate_graph(Graph.model_validate(data))


def codes(data: dict) -> set[str]:
    return {e["code"] for e in errors_for(data)}


def test_default_graph_is_valid(default_graph):
    assert errors_for(default_graph) == []


def test_handoff_example_graph_is_valid(full_graph):
    assert errors_for(full_graph) == []


def test_missing_agent(default_graph):
    default_graph["nodes"] = [n for n in default_graph["nodes"] if n["type"] != "agent"]
    default_graph["edges"] = [e for e in default_graph["edges"] if "n4" not in (e["from"], e["to"])]
    errs = errors_for(default_graph)
    assert [e["code"] for e in errs] == ["agent_count"]
    assert "exactly one 'agent'" in errs[0]["message"]


def test_two_inputs(default_graph):
    default_graph["nodes"].append({"id": "n9", "type": "input", "config": {}})
    assert "input_count" in codes(default_graph)


def test_two_outputs(default_graph):
    default_graph["nodes"].append({"id": "n9", "type": "output", "config": {}})
    assert "output_count" in codes(default_graph)


def test_gap_in_main_line(default_graph):
    default_graph["edges"] = [e for e in default_graph["edges"] if e != {"from": "n2", "to": "n3"}]
    errs = errors_for(default_graph)
    assert [e["code"] for e in errs] == ["main_line_gap"]
    assert "guardrails -> router" in errs[0]["message"]


def test_shortcut_skipping_guardrails(default_graph):
    default_graph["edges"].append({"from": "n1", "to": "n4"})
    assert "main_line_shortcut" in codes(default_graph)


def test_unconnected_source(full_graph):
    full_graph["edges"] = [e for e in full_graph["edges"] if e["from"] != "s2"]
    errs = errors_for(full_graph)
    assert [(e["code"], e["node_id"]) for e in errs] == [("not_connected", "s2")]
    assert "Draw a line" in errs[0]["message"]


def test_unconnected_tool(full_graph):
    full_graph["edges"] = [e for e in full_graph["edges"] if e["from"] != "t1"]
    assert [(e["code"], e["node_id"]) for e in errors_for(full_graph)] == [("not_connected", "t1")]


def test_source_wired_to_router_instead_of_agent(full_graph):
    for e in full_graph["edges"]:
        if e["from"] == "s1":
            e["to"] = "n3"
    assert {"not_connected", "wrong_target"} <= codes(full_graph)


def test_mcp_tool_must_be_read_only(full_graph):
    full_graph["nodes"][-1]["config"]["mode"] = "read_write"
    errs = errors_for(full_graph)
    assert [(e["code"], e["node_id"]) for e in errs] == [("mcp_not_read_only", "t2")]


def test_mcp_write_allowed_when_explicitly_enabled(full_graph):
    full_graph["nodes"][-1]["config"].update({"mode": "read_write", "allow_write": True})
    assert errors_for(full_graph) == []


def test_mcp_without_mode_is_rejected(full_graph):
    del full_graph["nodes"][-1]["config"]["mode"]
    assert "mcp_not_read_only" in codes(full_graph)


def test_unknown_type(default_graph):
    default_graph["nodes"].append({"id": "x1", "type": "source_ftp", "config": {}})
    assert "unknown_type" in codes(default_graph)


def test_duplicate_ids(default_graph):
    default_graph["nodes"].append({"id": "n1", "type": "tool_calculator", "config": {}})
    assert "duplicate_id" in codes(default_graph)


def test_edge_to_missing_node(default_graph):
    default_graph["edges"].append({"from": "ghost", "to": "n4"})
    assert "unknown_edge_node" in codes(default_graph)


def test_bad_agent_language(default_graph):
    default_graph["nodes"][3]["config"]["language"] = "fr"
    assert codes(default_graph) == {"bad_language"}


def test_all_errors_reported_together(full_graph):
    full_graph["edges"] = [e for e in full_graph["edges"] if e["from"] not in ("s1", "t2")]
    full_graph["nodes"][-1]["config"]["mode"] = "write"
    assert len(errors_for(full_graph)) == 3
