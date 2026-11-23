"""Shared test helpers: puts api/ on the import path and loads the example graphs."""

import copy
import json
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))

EXAMPLES = API_DIR / "app" / "pipeline" / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def default_graph() -> dict:
    return copy.deepcopy(_load("default.json"))


@pytest.fixture
def full_graph() -> dict:
    return copy.deepcopy(_load("full.json"))
