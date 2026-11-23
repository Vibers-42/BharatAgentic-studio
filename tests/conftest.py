"""Shared test helpers: puts api/ and samples/ on the import path, loads the example graphs, and
replaces retrieval and Gemini with offline fakes so pipeline tests never need Ollama or a key."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(ROOT / "samples"))
sys.path.insert(0, str(ROOT / "tests"))

EXAMPLES = API_DIR / "app" / "pipeline" / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def default_graph() -> dict:
    return copy.deepcopy(_load("default.json"))


@pytest.fixture
def full_graph() -> dict:
    return copy.deepcopy(_load("full.json"))


def fake_stream(system: str, prompt: str):
    yield {"text": "Hello "}
    yield {"text": "there."}
    yield {"usage": {"input_tokens": 10, "output_tokens": 2}}


def pytest_configure(config):
    config.addinivalue_line("markers", "real_models: use the real retrieve() and provider stream() functions")
    config.addinivalue_line("markers", "real_usage: resolve the user and log usage in the database")


@pytest.fixture(autouse=True)
def offline_models(monkeypatch, request):
    """By default: no chunks are found, every model answers 'Hello there.', retry waits are skipped,
    and usage is not logged. Tests can override any of these."""
    from app.models import gemini, generate, groq, ollama
    from app.pipeline.stages import agent
    from app.routes import pipeline

    monkeypatch.setattr(generate, "sleep", lambda seconds: None)
    if "real_usage" not in request.keywords:
        monkeypatch.setattr(pipeline, "resolve_user", lambda name: None)
    if "real_models" in request.keywords:
        return
    monkeypatch.setattr(agent, "retrieve", lambda question, source_ids, scope="inline": ([], []))
    from app.rag import claims
    monkeypatch.setattr(claims, "verify", lambda answer, chunks: {"answer": answer, "claims": [], "removed": 0})
    for provider in (gemini, groq, ollama):
        monkeypatch.setattr(provider, "stream", fake_stream)
