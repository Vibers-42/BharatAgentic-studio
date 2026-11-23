"""Pipeline endpoints: get the default graph, validate a graph, and run a question through it.
/pipeline/run streams Server-Sent Events (one per stage change); add ?stream=false for plain JSON.
The request may pick the model and names the user whose usage is logged."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
    graph_id: int | None = None   # ... or the id of a saved graph
    model: str | None = None      # "gemini", "groq" or "local"; None uses the agent node's setting
    user: str = "demo"            # whose usage this is (the user switcher)


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
    if (req.graph is None) == (req.graph_id is None):
        raise HTTPException(400, "Send exactly one of 'graph' or 'graph_id'.")
    graph = req.graph if req.graph is not None else load_graph(req.graph_id)

    # Validate before streaming, so a broken graph gets a normal 422 with every error listed.
    errors = validate_graph(graph)
    if errors:
        raise HTTPException(422, {"message": "The pipeline graph is not valid.", "errors": errors})

    if req.model is not None and req.model not in MODELS:
        raise HTTPException(400, f"Unknown model '{req.model}'. Choose one of: {', '.join(MODELS)}.")
    uid = resolve_user(req.user)  # 400 for an unknown user, before anything runs

    # A saved graph searches only its own chunks; a graph sent with the question uses the "inline" scope.
    events = run_pipeline(graph, req.question, req.language, req.model, uid, graph_scope(req.graph_id))
    if not stream:
        return {"events": list(events)}
    return StreamingResponse(
        (_sse(e) for e in events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
