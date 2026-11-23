"""Pipeline endpoints: get the default graph, validate a graph, and run a question through it.
/pipeline/run streams Server-Sent Events (one per stage change); add ?stream=false for plain JSON.
The request may pick the model and names the user whose usage is logged."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..models.generate import is_local
from ..pipeline.engine import run_pipeline
from ..pipeline.graph import MODELS, Graph
from ..pipeline.validate import validate_graph
from ..rag.store import graph_scope
from .graphs import load_graph
from .users import user_id

router = APIRouter(prefix="/pipeline")

DEFAULT_GRAPH = Path(__file__).resolve().parent.parent / "pipeline" / "examples" / "default.json"


class RunRequest(BaseModel):
    question: str
    language: str | None = None   # "en", "te" or "hi"; None lets the input stage decide
    graph: Graph | None = None    # send the graph inline ...
    graph_id: int | None = None   # ... or the id of a saved graph; with both, the inline graph runs (the
                                  # canvas's unsaved edits) and graph_id says whose documents are searched
    model: str | None = None      # "gemini", "groq", "local" or "local_small"; None uses the agent node's setting
    user: str = "demo"            # whose usage this is (the user switcher)
    local_only: bool = False      # privacy: never call a cloud LLM API for this question, not even as fallback


def resolve_user(name: str) -> int:
    """The user's id for usage logging; a module-level function so tests without a database can skip it."""
    return user_id(name)


@router.get("/default")
def default_graph():
    return json.loads(DEFAULT_GRAPH.read_text(encoding="utf-8"))


@router.post("/validate")
def validate(graph: Graph):
    errors = validate_graph(graph)
    return {"valid": not errors, "errors": errors}


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/run")
def run(req: RunRequest, stream: bool = True):
    if req.graph is None and req.graph_id is None:
        raise HTTPException(400, "Send 'graph', 'graph_id', or both (the canvas graph run against a saved graph's documents).")
    saved = load_graph(req.graph_id) if req.graph_id is not None else None  # 404 for an unknown id
    graph = req.graph if req.graph is not None else saved

    # Validate before streaming, so a broken graph gets a normal 422 with every error listed.
    errors = validate_graph(graph)
    if errors:
        raise HTTPException(422, {"message": "The pipeline graph is not valid.", "errors": errors})

    if req.model is not None and req.model not in MODELS:
        raise HTTPException(400, f"Unknown model '{req.model}'. Choose one of: {', '.join(MODELS)}.")
    # Local-only (from the request, the agent node, or LOCAL_ONLY in .env): refuse a cloud model up front,
    # so not even the question is sent anywhere.
    agent = graph.of_type("agent")[0]
    local_only = req.local_only or agent.config.get("local_only") is True or settings.local_only
    model = req.model or agent.config.get("model", "gemini")
    if local_only and not is_local(model):
        raise HTTPException(400, f"Local-only mode is on, so the cloud model '{model}' cannot be used. "
                                 "Choose 'local' or 'local_small'.")
    uid = resolve_user(req.user)  # 400 for an unknown user, before anything runs

    # A saved graph searches only its own chunks; a graph sent with the question uses the "inline" scope.
    events = run_pipeline(graph, req.question, req.language, req.model, uid, graph_scope(req.graph_id), local_only)
    if not stream:
        return {"events": list(events)}
    return StreamingResponse(
        (_sse(e) for e in events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
