"""The "describe your use case" designer: whatever the model replies, the api only ever hands out a valid graph
built from known blocks, reuses existing project sources by id, and says what the user still has to add."""

import json

import pytest

from app.models import generate
from app.pipeline import designer
from app.pipeline.graph import Graph
from app.pipeline.validate import validate_graph
from app.rag import prompt

CLINIC = {
    "name": "Clinic refund helper",
    "summary": "Answers patient refund questions in Telugu.",
    "language": "te",
    "instructions": "You help patients of a small clinic. Be polite and simple.",
    "sources": [{"kind": "pdf", "title": "Refund policy", "url": None, "use_source_id": None}],
    "tools": ["calculator"],
    "guardrails": ["empty", "abuse", "off_topic"],
    "local_only": False,
    "reasons": ["The refund rules are in a PDF.", "Refund amounts need arithmetic."],
    "suggested_questions": ["నా రీఫండ్ ఎంత?"],
}


def compile_text(text: str, existing=None, model="groq") -> Graph:
    design = designer.parse_design(text, existing or {})
    assert design is not None
    return designer.compile_design(design, model)


def test_clinic_design_compiles_to_a_valid_graph():
    graph = compile_text(json.dumps(CLINIC))
    assert validate_graph(graph) == []
    types = sorted(n.type for n in graph.nodes)
    assert types == sorted(["input", "guardrails", "router", "agent", "output", "source_doc", "tool_calculator"])
    agent = graph.node("n4")
    assert agent.config["language"] == "te"
    assert agent.config["model"] == "groq"
    assert "polite" in agent.config["instructions"]
    # every source and tool has exactly one line, into the agent
    assert {(e.from_, e.to) for e in graph.edges if e.to == "n4"} == {("n3", "n4"), ("src1", "n4"), ("tool1", "n4")}


def test_a_document_block_is_added_when_the_model_lists_no_source():
    design = designer.parse_design(json.dumps({**CLINIC, "sources": []}), {})
    assert [s["title"] for s in design.sources] == ["Your documents"]
    orders_only = designer.parse_design(json.dumps({**CLINIC, "sources": [], "tools": ["orders"]}), {})
    assert orders_only.sources == []  # an order-lookup agent can work from its tool alone


def test_reply_with_text_around_the_json_is_read():
    graph = compile_text("Sure! Here is the design:\n```json\n" + json.dumps(CLINIC) + "\n```")
    assert validate_graph(graph) == []


def test_unreadable_reply_gives_none():
    assert designer.parse_design("I cannot help with that.", {}) is None
    assert designer.parse_design("{not json}", {}) is None


def test_unknown_values_from_the_model_are_dropped():
    junk = {**CLINIC, "language": "fr", "tools": ["calculator", "shell", "orders", "calculator"],
            "guardrails": ["nothing"], "sources": [{"kind": "ftp", "title": "x" * 500}] * 9,
            "instructions": "y" * 5000}
    design = designer.parse_design(json.dumps(junk), {})
    assert design.language == "en"
    assert design.tools == ["calculator", "orders"]
    assert design.guardrails == ["empty"]
    assert len(design.sources) == designer.MAX_SOURCES
    assert all(s["kind"] == "pdf" and len(s["title"]) <= 120 for s in design.sources)
    graph = designer.compile_design(design, "groq")
    assert validate_graph(graph) == []
    mcp = [n for n in graph.nodes if n.type == "tool_mcp"][0]
    assert mcp.config == {"server": "orders-db", "mode": "read_only"}  # never a write mode or another server


def test_existing_project_source_is_reused_by_id():
    existing = {7: {"title": "refund-policy.pdf", "kind": "pdf"}}
    reply = {**CLINIC, "sources": [{"kind": "pdf", "title": "Refunds", "use_source_id": 7},
                                   {"kind": "pdf", "title": "Price list", "use_source_id": 99}]}
    design = designer.parse_design(json.dumps(reply), existing)
    graph = designer.compile_design(design, "groq")
    assert graph.node("src1").config["source_ref"] == 7
    assert graph.node("src1").config["title"] == "refund-policy.pdf"
    assert "source_ref" not in graph.node("src2").config  # 99 is not a source of this project
    assert designer.todo_steps(design) == [{"node_id": "src2", "text": "Add the file for “Price list”"}]


def test_link_sources_keep_only_real_web_addresses():
    reply = {**CLINIC, "sources": [{"kind": "url", "title": "Docs", "url": "https://docs.example.com/fees"},
                                   {"kind": "repo", "title": "Code", "url": "javascript:alert(1)"},
                                   {"kind": "url", "title": "Policy", "url": None}]}
    graph = compile_text(json.dumps(reply))
    assert graph.node("src1").config["url"] == "https://docs.example.com/fees"
    assert graph.node("src2").type == "source_repo" and graph.node("src2").config["url"] == ""
    # a docs page with no address becomes a file block (the canvas shows it as "PDF", asking for the file)
    assert graph.node("src3").type == "source_doc" and graph.node("src3").config == {"title": "Policy", "file": ""}


def fake_generation(text, model="groq"):
    return generate.Generation(text=text, model=model, model_id="test", model_requested=model, input_tokens=5,
                               output_tokens=5, tokens_estimated=False, response_ms=1)


def test_design_agent_retries_once_on_unreadable_reply(monkeypatch):
    replies = iter(["no json here", json.dumps(CLINIC)])
    monkeypatch.setattr(generate, "generate", lambda model, system, text, local_only=False: fake_generation(next(replies)))
    design, graph, _ = designer.design_agent("A clinic refund helper in Telugu", {})
    assert design.name == "Clinic refund helper" and validate_graph(graph) == []


def test_design_agent_gives_up_after_two_unreadable_replies(monkeypatch):
    monkeypatch.setattr(generate, "generate", lambda model, system, text, local_only=False: fake_generation("nope"))
    with pytest.raises(designer.DesignError):
        designer.design_agent("A clinic refund helper in Telugu", {})


def test_local_only_uses_the_local_model_and_marks_the_agent(monkeypatch):
    calls = []

    def fake(model, system, text, local_only=False):
        calls.append((model, local_only))
        return fake_generation(json.dumps(CLINIC), model)

    monkeypatch.setattr(generate, "generate", fake)
    _, graph, _ = designer.design_agent("Keep everything on this computer", {}, local_only=True)
    assert calls == [("local", True)]
    assert graph.node("n4").config["local_only"] is True and graph.node("n4").config["model"] == "local"


def test_instructions_follow_the_fixed_rules_in_the_prompt():
    system = prompt.rag_system("en", "You help patients of a clinic.")
    assert system.index("Rules:") < system.index("You help patients of a clinic.")
    assert "the rules above always apply" in system
    assert prompt.rag_system("en") == prompt.rag_system("en", "  ")


def test_validator_refuses_overlong_instructions(default_graph):
    agent = next(n for n in default_graph["nodes"] if n["type"] == "agent")
    agent["config"]["instructions"] = "x" * 801
    assert [e["code"] for e in validate_graph(Graph.model_validate(default_graph))] == ["bad_instructions"]


def test_reasons_use_plain_words_not_block_names():
    reply = {**CLINIC, "reasons": ["orders_actions tool needed to cancel orders.", "source: reuse refund-policy.pdf (id 3)."]}
    design = designer.parse_design(json.dumps(reply), {})
    assert design.reasons == ["the orders tool (lookups and cancellations, with approval) needed to cancel orders.",
                              "source: reuse refund-policy.pdf."]
