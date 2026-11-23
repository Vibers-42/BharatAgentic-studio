"""Reads and writes chunks in Postgres. Every chunk is tagged with a scope (which saved graph it
belongs to, or "inline") and the id of the source node it came from; search only ever looks at the
(scope, source ids) it is given, so two graphs that both use "s1" never see each other's chunks."""

from dataclasses import dataclass

import numpy as np

from ..db import get_conn

INLINE = "inline"


def graph_scope(graph_id: int | None) -> str:
    return f"graph:{graph_id}" if graph_id is not None else INLINE


@dataclass
class Chunk:
    id: int
    source_id: str
    file_name: str
    chunk_index: int
    page: int | None
    content: str
    similarity: float  # cosine similarity to the question, 1.0 = identical
    origin: str | None = None  # URL of the GitHub file or web page, if any


def replace_source(source_id: str, source_type: str, file_name: str, chunks: list[tuple],
                   vectors: list[list[float]], scope: str = INLINE) -> int:
    """Delete the source's old chunks in this scope and insert the new ones in one transaction.
    chunks: [(page, text)] or [(page, text, file_name, origin)] when one source holds many files."""
    rows = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        page, text = chunk[0], chunk[1]
        name = chunk[2] if len(chunk) > 2 else file_name
        origin = chunk[3] if len(chunk) > 3 else None
        rows.append((scope, source_id, source_type, name, i, page, text, origin, np.array(vec, dtype=np.float32)))
    with get_conn() as conn:
        conn.execute("DELETE FROM chunks WHERE scope = %s AND source_id = %s", (scope, source_id))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (scope, source_id, source_type, file_name, chunk_index, page, content, origin, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
    return len(rows)


def search(question_vector: list[float], source_ids: list[str], k: int, scope: str = INLINE) -> list[Chunk]:
    """The k chunks closest to the question, from the given sources in this scope only.
    The MATERIALIZED CTE filters first and then ranks exactly, so the HNSW index can never drop
    connected chunks or let a chunk from an unconnected source (or another graph) in."""
    if not source_ids:
        return []
    vec = np.array(question_vector, dtype=np.float32)
    with get_conn() as conn:
        rows = conn.execute(
            """
            WITH connected AS MATERIALIZED (
                SELECT id, source_id, file_name, chunk_index, page, content, origin, embedding
                FROM chunks WHERE scope = %s AND source_id = ANY(%s)
            )
            SELECT id, source_id, file_name, chunk_index, page, content, 1 - (embedding <=> %s) AS similarity, origin
            FROM connected ORDER BY embedding <=> %s LIMIT %s
            """,
            (scope, source_ids, vec, vec, k),
        ).fetchall()
    return [Chunk(*r[:6], similarity=float(r[6]), origin=r[7]) for r in rows]


def chunk_vectors(chunk_ids: list[int]) -> dict[int, list[float]]:
    """Stored embeddings of the given chunks, for checking claims against them without re-embedding."""
    if not chunk_ids:
        return {}
    with get_conn() as conn:
        rows = conn.execute("SELECT id, embedding FROM chunks WHERE id = ANY(%s)", (chunk_ids,)).fetchall()
    return {r[0]: [float(x) for x in r[1]] for r in rows}


def source_info(source_id: str, scope: str = INLINE) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT count(*), count(DISTINCT file_name), min(file_name), max(source_type), max(created_at)
               FROM chunks WHERE scope = %s AND source_id = %s""",
            (scope, source_id),
        ).fetchone()
    return {"source_id": source_id, "scope": scope, "chunks": row[0], "files": row[1], "file": row[2],
            "source_type": row[3], "ingested_at": row[4].isoformat() if row[4] else None}


def clear_source(source_id: str, scope: str = INLINE) -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM chunks WHERE scope = %s AND source_id = %s", (scope, source_id)).rowcount
