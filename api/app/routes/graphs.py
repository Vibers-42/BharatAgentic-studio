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
    return graph.model_dump(by_alias=True)  # keeps the "from" key on edges


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


@router.get("")
def list_graphs():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, updated_at FROM graphs ORDER BY id DESC").fetchall()
    return [{"id": r[0], "name": r[1], "updated_at": r[2].isoformat()} for r in rows]


@router.get("/{graph_id}")
def get_graph(graph_id: int):
    return _to_json(load_graph(graph_id))
