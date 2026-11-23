"""Saved pipelines: store the canvas graph JSON in Postgres and load it back by id.
A graph is only saved if it passes validation, so a saved graph can always run."""

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..db import get_conn
from ..pipeline.graph import Graph
from ..pipeline.validate import validate_graph

router = APIRouter(prefix="/graphs")


class SaveRequest(BaseModel):
    name: str
    graph: Graph


def _to_json(graph: Graph) -> dict:
    # by_alias keeps the "from" key on edges; exclude_none leaves out positions a graph does not have
    return graph.model_dump(by_alias=True, exclude_none=True)


def load_graph(graph_id: int) -> Graph:
    with get_conn() as conn:
        row = conn.execute("SELECT graph FROM graphs WHERE id = %s", (graph_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"No saved graph with id {graph_id}.")
    return Graph.model_validate(row[0])


@router.post("", status_code=201)
def save_graph(req: SaveRequest):
    errors = validate_graph(req.graph)
    if errors:
        raise HTTPException(422, {"message": "The pipeline graph is not valid.", "errors": errors})
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO graphs (name, graph) VALUES (%s, %s) RETURNING id",
            (req.name, Jsonb(_to_json(req.graph))),
        ).fetchone()
    return {"id": row[0], "name": req.name}


@router.put("/{graph_id}")
def update_graph(graph_id: int, req: SaveRequest):
    """Save the canvas over an existing graph. Its documents stay with it (same id, same scope)."""
    errors = validate_graph(req.graph)
    if errors:
        raise HTTPException(422, {"message": "The pipeline graph is not valid.", "errors": errors})
    with get_conn() as conn:
        row = conn.execute("UPDATE graphs SET name = %s, graph = %s, updated_at = now() WHERE id = %s RETURNING id",
                           (req.name, Jsonb(_to_json(req.graph)), graph_id)).fetchone()
    if row is None:
        raise HTTPException(404, f"No saved graph with id {graph_id}.")
    return {"id": graph_id, "name": req.name}


@router.get("")
def list_graphs():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, updated_at FROM graphs ORDER BY id DESC").fetchall()
    return [{"id": r[0], "name": r[1], "updated_at": r[2].isoformat()} for r in rows]


@router.get("/{graph_id}")
def get_graph(graph_id: int):
    return _to_json(load_graph(graph_id))


@router.post("/{graph_id}/ingest")
def ingest_graph_sources(graph_id: int):
    """Ingest every link-based source of a saved graph from its node settings (repo url, docs url),
    into this graph's own scope. PDF sources need an upload: POST /sources/{id}/pdf?graph_id=..."""
    from ..rag.ingest import IngestError, ingest_repo, ingest_url
    from ..rag.store import graph_scope

    graph, scope, results = load_graph(graph_id), graph_scope(graph_id), []
    for node in graph.nodes:
        url = node.config.get("url")
        try:
            if node.type == "source_repo" and url:
                results.append({"node": node.id, "ok": True, **ingest_repo(node.id, url, scope)})
            elif node.type == "source_doc" and url:
                results.append({"node": node.id, "ok": True, **ingest_url(node.id, url, scope)})
            elif node.type in ("source_doc", "source_repo"):
                results.append({"node": node.id, "ok": False, "error": "No url in the node config; upload a PDF with "
                                f"POST /sources/{node.id}/pdf?graph_id={graph_id}."})
        except (IngestError, ValueError) as exc:  # ValueError covers a refused private address
            results.append({"node": node.id, "ok": False, "error": str(exc)})
    return {"graph_id": graph_id, "scope": scope, "sources": results}
