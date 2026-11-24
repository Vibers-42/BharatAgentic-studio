"""Pipeline endpoints: validate a graph, and run a question. /pipeline/run streams Server-Sent Events
(one per stage change); add ?stream=false for plain JSON.

Who may run what (Phase 8), always decided from the database, never from the request:
- a saved agent (graph_id): anyone who can view its project; sending an edited, unsaved copy of it (graph +
  graph_id) needs edit rights on that agent;
- an unsaved graph in a project (graph + project_id): contributors of the project (the builder's test run);
- project chat (project_id, no graph): anyone who can view the project; the api builds the graph itself from
  the project's ready sources and the calculator;
- a conversation (conversation_id): only its owner, who must still have access to its project.
Every source node must point at a ready source of THAT project; anything else is refused before the model is
called. Local-only is the strictest of: the request, the agent, the project, the server. While the answer
streams, access is checked again every few seconds, so removing someone from the project stops their stream."""

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .. import knowledge
from ..access import project_access, require_conversation, require_graph, require_project
from ..auth import User, current_user
from ..config import settings
from ..db import get_conn
from ..models.generate import is_local
from ..pipeline.engine import run_pipeline
from ..pipeline.graph import MODELS, SOURCE_TYPES, Graph
from ..pipeline.validate import validate_graph
from .graphs import check_sources, source_refs

router = APIRouter(prefix="/pipeline")

DEFAULT_GRAPH = Path(__file__).resolve().parent.parent / "pipeline" / "examples" / "default.json"
RECHECK_SECONDS = 2.0


class RunRequest(BaseModel):
    question: str
    language: str | None = None   # "en", "te" or "hi"; None lets the input stage decide
    graph: Graph | None = None    # an unsaved graph (builder test run)
    graph_id: int | None = None   # a saved agent
    project_id: int | None = None  # project chat, or the project of an unsaved graph
    conversation_id: int | None = None  # save the question and answer in this conversation
    request_id: str | None = None  # client id for this question; a retry with the same id is refused (no duplicates)
    model: str | None = None      # "gemini", "groq", "local" or "local_small"; None uses the agent node's setting
    local_only: bool = False      # privacy: never call a cloud LLM API for this question, not even as fallback


@router.get("/default")
def default_graph(user: User = Depends(current_user)):
    return json.loads(DEFAULT_GRAPH.read_text(encoding="utf-8"))


@router.post("/validate")
def validate(graph: Graph, project_id: int | None = None, user: User = Depends(current_user)):
    errors = validate_graph(graph)
    if project_id is not None:
        require_project(user, project_id)
        errors += check_sources(graph, project_id)
    return {"valid": not errors, "errors": errors}


def project_chat_graph(project_id: int) -> Graph:
    """The default project chat: the five blocks, every ready source of the project, and the calculator."""
    ready = knowledge.ready_sources(project_id)
    nodes = [{"id": "n1", "type": "input", "config": {}},
             {"id": "n2", "type": "guardrails", "config": {"block": ["empty", "abuse", "off_topic"]}},
             {"id": "n3", "type": "router", "config": {}},
             {"id": "n4", "type": "agent", "config": {"model": "local", "language": "en"}},
             {"id": "n5", "type": "output", "config": {}},
             {"id": "calc", "type": "tool_calculator", "config": {}}]
    edges = [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}, {"from": "n3", "to": "n4"}, {"from": "n4", "to": "n5"},
             {"from": "calc", "to": "n4"}]
    for sid, info in ready.items():
        node = f"source_{sid}"
        nodes.append({"id": node, "type": "source_repo" if info["kind"] == "repo" else "source_doc",
                      "config": {"source_ref": sid, "title": info["title"]}})
        edges.append({"from": node, "to": "n4"})
    return Graph.model_validate({"nodes": nodes, "edges": edges})


def _source_map(graph: Graph, project_id: int) -> dict[str, str]:
    """node id -> chunk source id for every connected source node, refusing anything not ready in this project."""
    refs = source_refs(graph)
    ready = knowledge.ready_sources(project_id, list(refs.values()) or [-1])
    errors = []
    for n in graph.nodes:
        if n.type not in SOURCE_TYPES:
            continue
        sid = refs.get(n.id)
        if sid is None:
            errors.append({"code": "source_not_selected", "node_id": n.id,
                           "message": f"Source '{n.id}' has no knowledge yet. Pick a project source or add one."})
        elif sid not in ready:
            errors.append({"code": "source_not_ready", "node_id": n.id,
                           "message": f"Source '{n.id}' is not a ready source of this project."})
    if errors:
        raise HTTPException(422, {"message": "The agent cannot run yet.", "errors": errors})
    return {node: knowledge.chunk_source_id(sid) for node, sid in refs.items()}


def _history(conversation_id: int) -> list[tuple[str, str]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT role, content FROM messages WHERE conversation_id = %s AND status = 'ok' "
                            "ORDER BY id DESC LIMIT %s", (conversation_id, settings.history_messages)).fetchall()
    return [(r[0], r[1]) for r in reversed(rows)]


def _save_message(conversation_id: int, role: str, content: str, request_id: str | None, status: str = "ok",
                  result: dict | None = None) -> None:
    r = result or {}
    cost = r.get("cost") or {}
    usage = r.get("usage") or {}
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO messages (conversation_id, role, content, status, model, model_requested, fallback, citations,
                                     tool_calls, input_tokens, output_tokens, cost_usd, cost_inr, request_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (conversation_id, role, content, status, r.get("model"), r.get("model_requested"),
             Jsonb(r.get("fallback")) if r.get("fallback") else None, Jsonb(r.get("citations") or []),
             Jsonb(r.get("tool_calls") or []), usage.get("input_tokens"), usage.get("output_tokens"),
             cost.get("usd"), cost.get("inr"), request_id))
        conn.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,))


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/run")
def run(req: RunRequest, stream: bool = True, user: User = Depends(current_user)):
    conversation = None
    if req.conversation_id is not None:
        conversation, access = require_conversation(user, req.conversation_id)
        if req.graph_id is None and conversation["graph_id"] is not None:
            req.graph_id = conversation["graph_id"]
        if req.project_id not in (None, conversation["project_id"]):
            raise HTTPException(400, "This conversation belongs to another project.")
        req.project_id = conversation["project_id"]

    saved = None
    if req.graph_id is not None:
        saved, access = require_graph(user, req.graph_id, "edit" if req.graph is not None else "view")
        if req.project_id not in (None, saved["project_id"]):
            raise HTTPException(400, "This agent belongs to another project.")
        graph = req.graph if req.graph is not None else Graph.model_validate(saved["graph"])
        project_id = saved["project_id"]
    elif req.project_id is not None:
        access = require_project(user, req.project_id, "contribute" if req.graph is not None else "view")
        graph = req.graph if req.graph is not None else project_chat_graph(req.project_id)
        project_id = req.project_id
    else:
        raise HTTPException(400, "Say what to run: graph_id (an agent), project_id (project chat), or conversation_id.")
    if conversation and conversation["graph_id"] != req.graph_id:
        raise HTTPException(400, "This conversation belongs to another agent (or to the project chat).")

    errors = validate_graph(graph) + check_sources(graph, project_id)
    if errors:
        raise HTTPException(422, {"message": "The pipeline graph is not valid.", "errors": errors})
    source_map = _source_map(graph, project_id)

    if req.model is not None and req.model not in MODELS:
        raise HTTPException(400, f"Unknown model '{req.model}'. Choose one of: {', '.join(MODELS)}.")
    agent = graph.of_type("agent")[0]
    local_only = (req.local_only or agent.config.get("local_only") is True or access.local_only or settings.local_only)
    model = req.model or agent.config.get("model", "gemini")
    if local_only and not is_local(model):
        why = "this project is local-only" if access.local_only else "local-only mode is on"
        raise HTTPException(400, f"Cloud models cannot be used because {why}. Choose 'local' or 'local_small'.")

    history = _history(conversation["id"]) if conversation else []
    if conversation:
        try:
            _save_message(conversation["id"], "user", req.question, req.request_id)
        except Exception as exc:
            if "messages_conversation_id_request_id_role_key" in str(exc):
                raise HTTPException(409, "This question was already sent (same request id).") from None
            raise
    owner = {"org_id": access.org_id, "project_id": project_id, "graph_id": req.graph_id,
             "conversation_id": conversation["id"] if conversation else None}
    events = run_pipeline(graph, req.question, req.language, req.model, user.id, knowledge.scope_of(project_id),
                          local_only, source_map=source_map, history=history, owner=owner)

    def guarded():
        """Re-check access while streaming; store the final answer (or the error) in the conversation."""
        last_check = time.monotonic()
        for ev in events:
            if time.monotonic() - last_check > RECHECK_SECONDS:
                last_check = time.monotonic()
                if project_access(user.id, project_id) is None:
                    events.close()
                    yield {"type": "error", "stage": "agent", "node_id": None, "code": "access_revoked",
                           "message": "Your access to this project was removed."}
                    return
            if conversation and ev["type"] == "result":
                if project_access(user.id, project_id) is None:
                    yield {"type": "error", "stage": "output", "node_id": None, "code": "access_revoked",
                           "message": "Your access to this project was removed."}
                    return
                _save_message(conversation["id"], "assistant", ev.get("answer") or "", req.request_id, "ok", ev)
            elif conversation and ev["type"] == "error":
                _save_message(conversation["id"], "assistant", f"No answer: {ev.get('message')}", req.request_id, "error")
            yield ev

    if not stream:
        return {"events": list(guarded())}
    return StreamingResponse(
        (_sse(e) for e in guarded()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
