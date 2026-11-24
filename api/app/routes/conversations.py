"""Conversations: private to the person who started them. Two people in the same project share its knowledge
but never see each other's conversations; workspace owners and admins cannot open them either (usage pages
show only totals). Opening a conversation also requires current access to its project, so removing someone from
a project closes their conversations in it too."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..access import require_conversation, require_graph, require_project
from ..auth import User, current_user
from ..db import get_conn

router = APIRouter(prefix="/conversations")


class NewConversation(BaseModel):
    project_id: int
    graph_id: int | None = None
    title: str = "New conversation"


class Rename(BaseModel):
    title: str


def _conv(r) -> dict:
    return {"id": r[0], "project_id": r[1], "graph_id": r[2], "title": r[3], "created_at": r[4].isoformat(),
            "updated_at": r[5].isoformat(), "messages": r[6] if len(r) > 6 else None}


@router.get("")
def list_conversations(project_id: int, user: User = Depends(current_user)):
    require_project(user, project_id)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.id, c.project_id, c.graph_id, c.title, c.created_at, c.updated_at,
                      (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id)
               FROM conversations c WHERE c.user_id = %s AND c.project_id = %s ORDER BY c.updated_at DESC LIMIT 200""",
            (user.id, project_id)).fetchall()
    return [_conv(r) for r in rows]


@router.post("", status_code=201)
def create_conversation(req: NewConversation, user: User = Depends(current_user)):
    require_project(user, req.project_id)
    if req.graph_id is not None:
        g, _ = require_graph(user, req.graph_id)
        if g["project_id"] != req.project_id:
            raise HTTPException(400, "This agent belongs to another project.")
    with get_conn() as conn:
        r = conn.execute("INSERT INTO conversations (user_id, project_id, graph_id, title) VALUES (%s, %s, %s, %s) "
                         "RETURNING id, project_id, graph_id, title, created_at, updated_at",
                         (user.id, req.project_id, req.graph_id, req.title.strip()[:200] or "New conversation")).fetchone()
    return _conv(r)


@router.get("/{conversation_id}")
def get_conversation(conversation_id: int, user: User = Depends(current_user)):
    conv, _ = require_conversation(user, conversation_id)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, role, content, status, model, model_requested, fallback, citations, tool_calls, input_tokens,
                      output_tokens, cost_usd, cost_inr, created_at FROM messages WHERE conversation_id = %s ORDER BY id""",
            (conversation_id,)).fetchall()
    keys = ["id", "role", "content", "status", "model", "model_requested", "fallback", "citations", "tool_calls",
            "input_tokens", "output_tokens", "cost_usd", "cost_inr", "created_at"]
    msgs = []
    for r in rows:
        m = dict(zip(keys, r))
        m["cost_usd"] = float(m["cost_usd"]) if m["cost_usd"] is not None else None
        m["cost_inr"] = float(m["cost_inr"]) if m["cost_inr"] is not None else None
        m["created_at"] = m["created_at"].isoformat()
        msgs.append(m)
    return {**conv, "messages": msgs}


@router.patch("/{conversation_id}")
def rename(conversation_id: int, req: Rename, user: User = Depends(current_user)):
    require_conversation(user, conversation_id)
    title = req.title.strip()[:200]
    if not title:
        raise HTTPException(422, "Enter a title.")
    with get_conn() as conn:
        conn.execute("UPDATE conversations SET title = %s, updated_at = now() WHERE id = %s", (title, conversation_id))
    return {"id": conversation_id, "title": title}


@router.delete("/{conversation_id}")
def delete(conversation_id: int, user: User = Depends(current_user)):
    require_conversation(user, conversation_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
    return {"deleted": conversation_id}
