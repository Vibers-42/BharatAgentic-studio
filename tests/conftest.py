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
    yield {"usage": {"input_tokens": 10, "output_tokens": 2}, "finish_reason": "STOP"}


def pytest_configure(config):
    config.addinivalue_line("markers", "real_models: use the real retrieve() and gemini.stream()")


@pytest.fixture(autouse=True)
def offline_models(monkeypatch, request):
    """By default: no chunks are found and Gemini answers 'Hello there.'. Tests can override either."""
    if "real_models" in request.keywords:
        return
    from app.models import gemini
    from app.pipeline.stages import agent

    monkeypatch.setattr(agent, "retrieve", lambda question, source_ids: ([], []))
    monkeypatch.setattr(gemini, "stream", fake_stream)
