"""Agents: saved pipelines that belong to one project. A source node points at a registry source of the same
project with config.source_ref; saving refuses a reference to a source of any other project, and the links
are mirrored in agent_sources (so deleting a source unlinks it, and deleting an agent never deletes
knowledge). Viewers can open and run agents; contributors create agents and edit their own; owners and admins
edit all."""

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..access import require_graph, require_project
from ..auth import User, current_user
from ..db import get_conn
from ..pipeline.graph import SOURCE_TYPES, Graph
from ..pipeline.validate import validate_graph

router = APIRouter(prefix="/graphs")


class SaveRequest(BaseModel):
    project_id: int | None = None   # required when creating; an agent never moves to another project
    name: str
    description: str = ""
    graph: Graph


def _to_json(graph: Graph) -> dict:
    # by_alias keeps the "from" key on edges; exclude_none leaves out positions a graph does not have
    return graph.model_dump(by_alias=True, exclude_none=True)


def source_refs(graph: Graph) -> dict[str, int]:
    """Source node id -> registry source id, for nodes that point at one."""
    refs = {}
    for n in graph.nodes:
        if n.type in SOURCE_TYPES and n.config.get("source_ref") is not None:
            try:
                refs[n.id] = int(n.config["source_ref"])
            except (TypeError, ValueError):
                raise HTTPException(422, {"message": "The agent is not valid.", "errors": [
                    {"code": "bad_source_ref", "node_id": n.id, "message": f"Source '{n.id}' has an invalid source reference."}]})
    return refs


def check_sources(graph: Graph, project_id: int) -> list[dict]:
    """Errors for source references that are not sources of this project (another project, or deleted)."""
    refs = source_refs(graph)
    if not refs:
        return []
    with get_conn() as conn:
        own = {r[0] for r in conn.execute("SELECT id FROM sources WHERE project_id = %s AND id = ANY(%s)",
                                         (project_id, list(refs.values()))).fetchall()}
    return [{"code": "source_not_in_project", "node_id": node, "message": f"Source '{node}' is not a source of this "
             "project (it may have been deleted). Pick one from the project's knowledge."}
            for node, sid in refs.items() if sid not in own]


def _check(graph: Graph, project_id: int) -> None:
    errors = validate_graph(graph) + check_sources(graph, project_id)
    if errors:
        raise HTTPException(422, {"message": "The agent is not valid.", "errors": errors})


def _sync_links(conn, graph_id: int, graph: Graph) -> None:
    conn.execute("DELETE FROM agent_sources WHERE graph_id = %s", (graph_id,))
    for node, sid in source_refs(graph).items():
        conn.execute("INSERT INTO agent_sources (graph_id, node_id, source_id) VALUES (%s, %s, %s)", (graph_id, node, sid))


def _meta(row: dict, access, user: User) -> dict:
    return {"id": row["id"], "project_id": row["project_id"], "name": row["name"], "description": row["description"],
            "created_by": row["created_by"], "updated_at": row["updated_at"].isoformat(),
            "can_edit": access.can_manage or (access.can_contribute and row["created_by"] == user.id)}


@router.post("", status_code=201)
def save_graph(req: SaveRequest, user: User = Depends(current_user)):
    if req.project_id is None:
        raise HTTPException(422, "Say which project the agent belongs to (project_id).")
    require_project(user, req.project_id, "contribute")
    _check(req.graph, req.project_id)
    with get_conn() as conn:
        gid = conn.execute("INSERT INTO graphs (name, description, graph, project_id, created_by) "
                           "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                           (req.name.strip()[:120] or "Agent", req.description.strip()[:1000], Jsonb(_to_json(req.graph)),
                            req.project_id, user.id)).fetchone()[0]
        _sync_links(conn, gid, req.graph)
    return {"id": gid, "name": req.name, "project_id": req.project_id}


@router.put("/{graph_id}")
def update_graph(graph_id: int, req: SaveRequest, user: User = Depends(current_user)):
    row, _ = require_graph(user, graph_id, "edit")
    if req.project_id is not None and req.project_id != row["project_id"]:
        raise HTTPException(400, "An agent cannot be moved to another project.")
    _check(req.graph, row["project_id"])
    with get_conn() as conn:
        conn.execute("UPDATE graphs SET name = %s, description = %s, graph = %s, updated_at = now() WHERE id = %s",
                     (req.name.strip()[:120] or "Agent", req.description.strip()[:1000], Jsonb(_to_json(req.graph)), graph_id))
        _sync_links(conn, graph_id, req.graph)
    return {"id": graph_id, "name": req.name, "project_id": row["project_id"]}


@router.get("")
def list_graphs(project_id: int, user: User = Depends(current_user)):
    access = require_project(user, project_id)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT g.id, g.project_id, g.name, g.description, g.graph, g.created_by, g.updated_at, g.created_at,
                      coalesce(u.full_name, 'imported'), (SELECT count(*) FROM agent_sources a WHERE a.graph_id = g.id)
               FROM graphs g LEFT JOIN users u ON u.id = g.created_by WHERE g.project_id = %s ORDER BY g.updated_at DESC""",
            (project_id,)).fetchall()
    out = []
    for r in rows:
        row = dict(zip(["id", "project_id", "name", "description", "graph", "created_by", "updated_at", "created_at"], r[:8]))
        agent = next((n for n in row["graph"].get("nodes", []) if n.get("type") == "agent"), {})
        out.append({**_meta(row, access, user), "created_by_name": r[8], "sources": r[9],
                    "model": agent.get("config", {}).get("model"), "local_only": agent.get("config", {}).get("local_only", False)})
    return out


@router.get("/{graph_id}")
def get_graph(graph_id: int, user: User = Depends(current_user)):
    row, access = require_graph(user, graph_id)
    return {**_to_json(Graph.model_validate(row["graph"])), "meta": _meta(row, access, user)}


@router.delete("/{graph_id}")
def delete_graph(graph_id: int, user: User = Depends(current_user)):
    """Deletes the agent only; the project's sources stay (other agents may use them)."""
    require_graph(user, graph_id, "edit")
    with get_conn() as conn:
        conn.execute("DELETE FROM graphs WHERE id = %s", (graph_id,))
    return {"deleted": graph_id}
