"""The Phase 3 agent: retrieval only from connected sources, streamed tokens, checked citations,
"I don't know" when nothing supports an answer, and the real Gemini client against a fake server."""

import dataclasses

import pytest

import fake_gemini
from app.config import settings
from app.models import gemini
from app.pipeline.engine import run_pipeline
from app.pipeline.graph import Graph
from app.pipeline.stages import agent
from app.rag.prompt import DONT_KNOW
from app.rag.store import Chunk

REFUND_CHUNK = Chunk(1, "s1", "refund-policy.pdf", 0, 1,
                     "If a lab test is cancelled before the sample is collected, 80 percent is refunded.", 0.71)


def run(graph: dict, question: str, language: str | None = None) -> list[dict]:
    return list(run_pipeline(Graph.model_validate(graph), question, language))


@pytest.fixture
def spy_retrieve(monkeypatch):
    """Records which source ids were searched and returns REFUND_CHUNK for any search."""
    calls = []

    def fake(question, source_ids):
        calls.append(list(source_ids))
        return ([REFUND_CHUNK], [REFUND_CHUNK]) if source_ids else ([], [])

    monkeypatch.setattr(agent, "retrieve", fake)
    return calls


def answer_with(monkeypatch, text: str):
    def fake(system, prompt):
        for word in text.split(" "):
            yield {"text": word + " "}
        yield {"usage": {"input_tokens": 50, "output_tokens": 9}, "finish_reason": "STOP"}
    monkeypatch.setattr(gemini, "stream", fake)


def only_s1(graph: dict) -> dict:
    """The handoff graph with s2 and both tools removed: only s1 is connected."""
    keep = {"n1", "n2", "n3", "n4", "n5", "s1"}
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] in keep]
    graph["edges"] = [e for e in graph["edges"] if e["from"] in keep]
    return graph


def test_cited_answer(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "80 percent is refunded if cancelled before collection [1].")
    events = run(only_s1(full_graph), "lab test refund?")
    result = events[-1]
    assert result["supported"] is True and result["grounded"] is True
    assert result["citations"][0]["file"] == "refund-policy.pdf"
    assert result["citations"][0]["page"] == 1 and result["citations"][0]["chunk"] == 0
    assert result["usage"] == {"input_tokens": 50, "output_tokens": 9}


def test_tokens_stream_between_agent_running_and_done(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "80 percent is refunded [1].")
    events = run(only_s1(full_graph), "lab test refund?")
    kinds = [(e["type"], e.get("stage"), e.get("status")) for e in events]
    start = kinds.index(("stage", "agent", "running"))
    end = kinds.index(("stage", "agent", "done"))
    tokens = [e for e in events[start:end] if e["type"] == "token"]
    assert len(tokens) >= 4 and all(t["node_id"] == "n4" for t in tokens)


def test_only_connected_sources_are_searched(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "Answer [1].")
    run(only_s1(full_graph), "refund?")
    assert spy_retrieve == [["s1"]]


def test_unconnected_source_never_searched(monkeypatch, spy_retrieve, full_graph):
    # s2 is in the graph but its line to the agent is removed: the graph is invalid and nothing runs.
    from app.pipeline.validate import validate_graph
    full_graph["edges"] = [e for e in full_graph["edges"] if e["from"] != "s2"]
    assert validate_graph(Graph.model_validate(full_graph))  # the API refuses to run this graph


def test_nothing_relevant_means_dont_know_without_model_call(monkeypatch, full_graph):
    monkeypatch.setattr(agent, "retrieve", lambda q, ids: ([], [REFUND_CHUNK]))
    called = []
    monkeypatch.setattr(gemini, "stream", lambda s, p: called.append(1) or iter(()))
    events = run(only_s1(full_graph), "What time does the hostel gate close?")
    result = events[-1]
    assert result["answer"] == DONT_KNOW[result["language"]]
    assert result["supported"] is False and result["citations"] == []
    assert called == []
    detail = next(e for e in events if e.get("stage") == "agent" and e.get("status") == "done")["detail"]
    assert detail["model_called"] is False and detail["retrieval"]["relevant"] == 0


def test_answer_without_valid_citation_is_replaced(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "The refund is 100 percent [4].")  # [4] does not exist
    result = run(only_s1(full_graph), "lab test refund?", "en")[-1]
    assert result["answer"] == DONT_KNOW["en"]
    assert result["supported"] is False and result["citations"] == []


def test_answer_with_no_citation_is_replaced(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "The refund is 80 percent.")
    assert run(only_s1(full_graph), "lab test refund?", "en")[-1]["answer"] == DONT_KNOW["en"]


def test_model_says_dont_know(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, DONT_KNOW["te"])
    result = run(only_s1(full_graph), "lab test refund?", "te")[-1]
    assert result["answer"] == DONT_KNOW["te"] and result["supported"] is False


def test_invalid_markers_removed_from_shown_answer(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "80 percent is refunded [1][6].")
    result = run(only_s1(full_graph), "lab test refund?", "en")[-1]
    assert "[6]" not in result["answer"] and "[1]" in result["answer"]


def test_direct_route_has_no_citations(monkeypatch, spy_retrieve, default_graph):
    result = run(default_graph, "Namaste!")[-1]
    assert result["grounded"] is False and result["citations"] == [] and spy_retrieve == []


def test_gemini_error_becomes_error_event(monkeypatch, default_graph):
    def broken(system, prompt):
        raise gemini.GeminiError("GEMINI_API_KEY is not set")
        yield  # pragma: no cover
    monkeypatch.setattr(gemini, "stream", broken)
    events = run(default_graph, "hello there")
    assert events[-1]["type"] == "error" and events[-1]["stage"] == "agent"
    assert "GEMINI_API_KEY" in events[-1]["message"]


# ---------- the real Gemini client against the fake Gemini server ----------
def use_settings(monkeypatch, **changes):
    """Settings is frozen, so give the gemini module a changed copy."""
    monkeypatch.setattr(gemini, "settings", dataclasses.replace(gemini.settings, **changes))


@pytest.fixture
def fake_server(monkeypatch):
    server = fake_gemini.start(0)
    monkeypatch.setattr(fake_gemini, "PAUSE_SECONDS", 0.01)
    use_settings(monkeypatch, gemini_base_url=f"http://127.0.0.1:{server.server_port}", gemini_api_key="test-key")
    yield server
    server.shutdown()


@pytest.mark.real_models
def test_gemini_client_streams_pieces_and_usage(fake_server):
    pieces = list(gemini.stream("system", "Sources:\n\n[1] (a.pdf, chunk 0)\nThe fee is Rs. 300.\n\nQuestion: fee?"))
    texts = [p["text"] for p in pieces if "text" in p]
    assert len(texts) >= 3
    assert "".join(texts).strip().endswith("[1]")
    assert pieces[-1]["usage"]["output_tokens"] > 0


@pytest.mark.real_models
def test_gemini_client_reports_quota(fake_server, monkeypatch):
    use_settings(monkeypatch, gemini_api_key="quota-exceeded")
    with pytest.raises(gemini.GeminiError, match="429"):
        list(gemini.stream("s", "p"))


@pytest.mark.real_models
def test_gemini_client_without_key(monkeypatch):
    use_settings(monkeypatch, gemini_api_key="")
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY is not set"):
        list(gemini.stream("s", "p"))


@pytest.mark.real_models
def test_gemini_request_body_has_system_and_no_thinking():
    body = gemini._body("rules", "question")
    assert body["systemInstruction"]["parts"][0]["text"] == "rules"
    assert body["contents"][0]["parts"][0]["text"] == "question"
    if "2.5-flash" in settings.gemini_model:
        assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
