"""The agent: retrieval only from connected sources, streamed tokens, checked citations, "I don't know"
when nothing supports an answer, fallback to the local model, and tokens and cost on every answer.
The provider clients themselves are tested in test_models.py."""

import pytest

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

    def fake(question, source_ids, scope="inline"):
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
    assert result["usage"] == {"input_tokens": 50, "output_tokens": 9, "estimated": False}


def test_tokens_stream_between_agent_running_and_done(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "80 percent is refunded [1].")
    events = run(only_s1(full_graph), "lab test refund?")
    kinds = [(e["type"], e.get("stage"), e.get("status")) for e in events]
    start = kinds.index(("stage", "agent", "running"))
    end = kinds.index(("stage", "agent", "done"))
    tokens = [e for e in events[start:end] if e["type"] == "token"]
    assert len(tokens) >= 4 and all(t["node_id"] == "n4" for t in tokens)
    assert all(isinstance(t["ms"], int) for t in tokens)
    assert [t["ms"] for t in tokens] == sorted(t["ms"] for t in tokens)


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
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline": ([], [REFUND_CHUNK]))
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


def test_failed_gemini_falls_back_to_local(monkeypatch, default_graph):
    def broken(system, prompt, **kwargs):
        raise gemini.GeminiError("GEMINI_API_KEY is not set", code="no_key")
        yield  # pragma: no cover
    monkeypatch.setattr(gemini, "stream", broken)
    events = run(default_graph, "hello there")
    fallback = next(e for e in events if e["type"] == "fallback")
    assert fallback["from"] == "gemini" and fallback["to"] == "local" and fallback["code"] == "no_key"
    result = events[-1]
    assert result["type"] == "result" and result["model"].startswith("local:")
    assert result["fallback"]["from"] == "gemini" and result["model_requested"] == "gemini"


def test_all_models_failing_gives_one_clear_error(monkeypatch, default_graph):
    from app.models import ollama

    def broken(code):
        def stream(system, prompt, **kwargs):
            raise gemini.GeminiError(f"{code} happened", code=code)
            yield  # pragma: no cover
        return stream
    monkeypatch.setattr(gemini, "stream", broken("quota_daily"))
    monkeypatch.setattr(ollama, "stream", broken("network_error"))
    events = run(default_graph, "hello there")
    error = events[-1]
    assert error["type"] == "error" and error["code"] == "all_providers_failed" and error["stage"] == "agent"
    assert [(a["model"], a["code"]) for a in error["attempts"]] == [
        ("gemini", "quota_daily"), ("local", "network_error"), ("local_small", "network_error")]
    assert "Every model failed" in error["message"]
    assert not any(e["type"] == "result" for e in events)


def test_model_choice_from_request_beats_agent_setting(default_graph):
    events = list(run_pipeline(Graph.model_validate(default_graph), "hello there", None, "groq"))
    assert events[-1]["model"].startswith("groq:") and events[-1]["model_requested"] == "groq"


def test_every_answer_carries_tokens_and_cost(default_graph):
    result = run(default_graph, "hello there")[-1]
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 2, "estimated": False}
    assert result["cost"]["priced"] is True and result["cost"]["usd"] > 0 and result["cost"]["api_fee"] is True
    assert {row["model"] for row in result["comparison"]} == {"gemini", "groq", "local", "local_small"}


def test_no_model_call_means_no_cost(monkeypatch, full_graph):
    monkeypatch.setattr(agent, "retrieve", lambda q, ids, scope="inline": ([], []))
    result = run(only_s1(full_graph), "What time does the hostel gate close?")[-1]
    assert result["model"] is None and result["cost"] is None and result["usage"] is None


def test_answer_in_the_wrong_language_is_flagged(monkeypatch, spy_retrieve, full_graph):
    # The small local model answered Telugu questions in English in the evaluation: that must show.
    answer_with(monkeypatch, "The hostel gate closes at 9:30 PM every day [1].")
    result = run(only_s1(full_graph), "హాస్టల్ గేట్ ఎన్ని గంటలకు మూసివేస్తారు?")[-1]
    assert result["language"] == "te" and result["language_match"] is False
    assert any("not written in Telugu" in w for w in result["warnings"])


def test_answer_in_the_right_language_is_not_flagged(monkeypatch, spy_retrieve, full_graph):
    answer_with(monkeypatch, "80 percent is refunded [1].")
    result = run(only_s1(full_graph), "lab test refund?", "en")[-1]
    assert result["language_match"] is True and result["warnings"] == []
