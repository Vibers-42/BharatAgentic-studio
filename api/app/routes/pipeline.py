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

from .. import budgets, knowledge, usage
from ..access import project_access, require_conversation, require_graph, require_project
from ..auth import User, current_user
from ..config import settings
from ..db import get_conn
from ..models import pricing
from ..models.generate import AllProvidersFailed, LocalOnlyError, is_local
from ..pipeline import designer
from ..tools import approvals
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


class DesignRequest(BaseModel):
    description: str
    project_id: int
    local_only: bool = False


@router.post("/design")
def design(req: DesignRequest, user: User = Depends(current_user)):
    """Describe the use case, get a ready agent: the model fills in a small design, the api compiles it into a
    valid graph (see pipeline/designer.py). Needs contributor rights, like drawing an agent by hand. Nothing is
    saved: the canvas opens the result and the user saves it. The model call is logged as usage."""
    access = require_project(user, req.project_id, "contribute")
    description = req.description.strip()
    if len(description) < 10:
        raise HTTPException(422, "Describe the use case in a sentence or two (at least 10 characters).")
    if len(description) > designer.MAX_DESCRIPTION:
        raise HTTPException(422, f"Please keep the description under {designer.MAX_DESCRIPTION} characters.")
    local_only = req.local_only or access.local_only or settings.local_only
    refusal, _ = budgets.check(req.project_id)
    if refusal and not local_only:
        raise HTTPException(402, refusal)
    existing = knowledge.ready_sources(req.project_id)
    try:
        plan, graph, gen = designer.design_agent(description, existing, local_only=local_only)
    except AllProvidersFailed as exc:
        raise HTTPException(503, f"No model could design the agent right now. {exc}")
    except (designer.DesignError, LocalOnlyError) as exc:
        raise HTTPException(422, str(exc))
    price = pricing.cost(gen.model_id, gen.input_tokens, gen.output_tokens, local=is_local(gen.model))
    try:
        usage.log(user_id=user.id, model=gen.label, model_requested=gen.model_requested,
                  input_tokens=gen.input_tokens, output_tokens=gen.output_tokens, tokens_estimated=gen.tokens_estimated,
                  cost_usd=price["usd"], cost_inr=price["inr"], response_ms=gen.response_ms,
                  local_compute_ms=gen.response_ms if is_local(gen.model) else 0, sources=[], language=plan.language,
                  fallback_from=gen.fallback["from"] if gen.fallback else None, org_id=access.org_id,
                  project_id=req.project_id)
    except Exception:  # never lose the design because the usage row could not be written
        pass
    return {
        "graph": graph.model_dump(by_alias=True, exclude_none=True),
        "name": plan.name,
        "summary": plan.summary,
        "reasons": plan.reasons,
        "suggested_questions": plan.suggested_questions,
        "todo": designer.todo_steps(plan),
        "reused_sources": [s["title"] for s in plan.sources if s.get("source_ref") is not None],
        "generation": {"model": gen.model, "label": gen.label, "ms": gen.response_ms,
                       "input_tokens": gen.input_tokens, "output_tokens": gen.output_tokens,
                       "cost_inr": price["inr"], "fallback": gen.fallback},
    }


class Decision(BaseModel):
    approve: bool


@router.post("/approvals/{approval_id}")
def decide_approval(approval_id: str, req: Decision, user: User = Depends(current_user)):
    """Approve or deny a paused tool call. Only the person whose question is waiting can decide, once; anyone
    else (or a decided / timed-out approval) gets 404."""
    decision = approvals.decide(approval_id, user.id, req.approve)
    if decision is None:
        raise HTTPException(404, "No such pending approval (it may have timed out or been decided already).")
    return {"approval_id": approval_id, "decision": decision}


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


def _history(conversation_id: int) -> tuple[list[tuple[str, str]], list[dict]]:
    """The last few messages of this conversation (only this conversation, which belongs to the caller), and
    what its latest answer cited. Failed answers are left out. The citations are only pointers: the agent
    re-reads those chunks and keeps them only if this run may still search their source."""
    with get_conn() as conn:
        rows = conn.execute("SELECT role, content, citations FROM messages WHERE conversation_id = %s AND status = 'ok' "
                            "ORDER BY id DESC LIMIT %s", (conversation_id, settings.history_messages)).fetchall()
    rows = list(reversed(rows))
    cited = next((r[2] or [] for r in reversed(rows) if r[0] == "assistant"), [])
    return [(r[0], r[1]) for r in rows], cited


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

    # Spend limits are checked before anything is saved or any model is called (local models cost nothing).
    refusal, budget_warnings = budgets.check(project_id, conversation["id"] if conversation else None)
    if refusal:
        if not is_local(model):
            raise HTTPException(402, refusal)
        local_only = True  # over the limit: a local model may still answer, but never fall back to a paid cloud API

    history, previous_citations = _history(conversation["id"]) if conversation else ([], [])
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
                          local_only, source_map=source_map, history=history, owner=owner,
                          previous_citations=previous_citations, can_approve=stream)

    def guarded():
        """Re-check access while streaming; store the final answer (or the error) in the conversation."""
        last_check = time.monotonic()
        for warning in budget_warnings:
            yield {"type": "budget", "stage": "input", "node_id": None, "level": "warning", "message": warning}
        for ev in events:
            if time.monotonic() - last_check > RECHECK_SECONDS:
                last_check = time.monotonic()
                if project_access(user.id, project_id) is None:
                    events.close()
                    yield {"type": "error", "stage": "agent", "node_id": None, "code": "access_revoked",
                           "message": "Your access to this project was removed."}
                    return
            if ev["type"] == "result" and project_access(user.id, project_id) is None:
                # Always checked right before the final answer (with its citations) leaves, whatever the timer says.
                yield {"type": "error", "stage": "output", "node_id": None, "code": "access_revoked",
                       "message": "Your access to this project was removed."}
                return
            if conversation and ev["type"] == "result":
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
