"""Questions about BharatAgent itself get the app's own description (not "I don't know"), and Telugu or Hindi typed
in English letters is answered in Telugu or Hindi."""

import pytest

from app.pipeline.stages.input import romanized_language
from app.pipeline.stages.router import about_app
from app.rag import prompt


@pytest.mark.parametrize("text,expected", [
    ("bharatagent gurinchi cheppu", "te"),
    ("refund enta vastundi", "te"),
    ("nenu cancel chesthe emi avutundi", "te"),
    ("refund kitna milega", "hi"),
    ("mujhe fees ke baare mein batao", "hi"),
    ("What is the refund policy?", None),
    ("How much is the hostel fee for 2 months?", None),
    ("Tell me about the admission dates", None),
])
def test_romanized_language(text, expected):
    assert romanized_language(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("bharatagent gurinchi cheppu", "name"),
    ("What is Bharat Agent Studio?", "name"),
    ("who are you", "you"),
    ("What can you do?", "you"),
    ("nuvvu evaru", "you"),
    ("तुम कौन हो", "you"),
    ("What is the refund policy?", None),
    ("What can you do about my refund for the cancelled lab test?", None),  # a real question, not "who are you"
    ("Can you tell me who are you supposed to call for refunds?", None),
])
def test_about_app(text, expected):
    assert about_app(text) == expected


def test_about_prompt_uses_only_the_fixed_description():
    system = prompt.about_system("te")
    assert prompt.ABOUT_APP in system and "Telugu" in system
    assert "ONLY this description" in system


def run_question(question, graph, monkeypatch, retrieved=()):
    """Run the real pipeline with a fake model and fake retrieval; returns (systems sent to the model, result)."""
    from app.models import generate
    from app.pipeline import engine
    from app.pipeline.graph import Graph
    from app.pipeline.stages import agent

    systems = []

    def fake_stream(model, system, text, local_only=False):
        systems.append(system)
        yield {"text": "BharatAgent Studio builds assistants."}
        yield {"done": generate.Generation(text="BharatAgent Studio builds assistants.", model="groq", model_id="t",
                                           model_requested="groq", input_tokens=1, output_tokens=1,
                                           tokens_estimated=False, response_ms=1)}

    monkeypatch.setattr(agent.generate, "stream", fake_stream)
    monkeypatch.setattr(agent, "_retrieve", lambda ctx, searchable: (list(retrieved), list(retrieved)))
    events = list(engine.run_pipeline(Graph.model_validate(graph), question))
    return systems, next(e for e in events if e["type"] == "result")


def test_romanized_prompts_ask_for_english_letters_and_keep_script_otherwise():
    roman = prompt.rag_system("te", roman=True)
    assert "English letters" in roman and "do not use Telugu or Devanagari script" in roman
    assert prompt.ROMAN_DONT_KNOW["te"] in roman and prompt.DONT_KNOW["te"] not in roman
    assert "English letters" not in prompt.rag_system("te")
    assert prompt.dont_know("en", roman=True) == prompt.DONT_KNOW["en"]


def test_romanized_dont_know_is_recognised():
    from app.rag.citations import is_dont_know
    assert is_dont_know(prompt.ROMAN_DONT_KNOW["te"]) and is_dont_know(prompt.ROMAN_DONT_KNOW["hi"])


def test_language_match_for_romanized_answers():
    from app.pipeline.stages.output import language_match
    assert language_match("Mee refund 960 rupees vastundi [1].", "te", roman=True)
    assert not language_match("మీ రీఫండ్ 960 రూపాయలు [1].", "te", roman=True)
    assert language_match("మీ రీఫండ్ 960 రూపాయలు [1].", "te")


def test_named_question_with_unhelpful_sources_falls_back_to_the_description(full_graph, monkeypatch):
    full_graph["nodes"] = [n for n in full_graph["nodes"] if n["type"] != "tool_mcp"]
    full_graph["edges"] = [e for e in full_graph["edges"] if e["from"] != "t2"]
    systems, result = run_question("bharatagent gurinchi cheppu", full_graph, monkeypatch)
    assert systems and prompt.ABOUT_APP in systems[-1]
    assert "English letters" in systems[-1]  # typed in English letters, so answered the same way
    assert result["language"] == "te" and result["roman"] is True and result["language_match"] is True
    assert result["answer"] == "BharatAgent Studio builds assistants."
    assert result["grounded"] is False and result["citations"] == []


def test_who_are_you_answers_directly_without_searching(default_graph, monkeypatch):
    systems, result = run_question("who are you?", default_graph, monkeypatch)
    assert len(systems) == 1 and prompt.ABOUT_APP in systems[0]
    assert result["grounded"] is False


def test_local_only_design_without_a_local_model_says_what_to_install(monkeypatch):
    from types import SimpleNamespace
    from app.routes import models as model_routes
    from app.routes import pipeline
    monkeypatch.setattr(model_routes, "_local_pulled", lambda model_id: False)
    steps = pipeline._local_model_step(SimpleNamespace(local_only=True))
    assert len(steps) == 1 and "ollama pull" in steps[0]["text"]
    monkeypatch.setattr(model_routes, "_local_pulled", lambda model_id: True)
    assert pipeline._local_model_step(SimpleNamespace(local_only=True)) == []
    assert pipeline._local_model_step(SimpleNamespace(local_only=False)) == []
