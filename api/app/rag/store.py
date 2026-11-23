"""Reads and writes chunks in Postgres. Every chunk is tagged with the id of the source node
it came from, and search only ever looks at the source ids it is given."""

from dataclasses import dataclass

import numpy as np

from ..db import get_conn


@dataclass
class Chunk:
    id: int
    source_id: str
    file_name: str
    chunk_index: int
    page: int | None
    content: str
    similarity: float  # cosine similarity to the question, 1.0 = identical


def replace_source(source_id: str, source_type: str, file_name: str,
                   chunks: list[tuple[int | None, str]], vectors: list[list[float]]) -> int:
    """Delete the source's old chunks and insert the new ones in one transaction."""
    with get_conn() as conn:
        conn.execute("DELETE FROM chunks WHERE source_id = %s", (source_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (source_id, source_type, file_name, chunk_index, page, content, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(source_id, source_type, file_name, i, page, text, np.array(vec, dtype=np.float32))
                 for i, ((page, text), vec) in enumerate(zip(chunks, vectors))],
            )
    return len(chunks)


def search(question_vector: list[float], source_ids: list[str], k: int) -> list[Chunk]:
    """The k chunks closest to the question, from the given sources only.
    The MATERIALIZED CTE filters by source first and then ranks exactly, so the HNSW index can never
    drop connected chunks or let a chunk from an unconnected source in."""
    if not source_ids:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """
            WITH connected AS MATERIALIZED (
                SELECT id, source_id, file_name, chunk_index, page, content, embedding
                FROM chunks WHERE source_id = ANY(%s)
            )
            SELECT id, source_id, file_name, chunk_index, page, content, 1 - (embedding <=> %s) AS similarity
            FROM connected ORDER BY embedding <=> %s LIMIT %s
            """,
            (source_ids, np.array(question_vector, dtype=np.float32),
             np.array(question_vector, dtype=np.float32), k),
        ).fetchall()
    return [Chunk(*r[:6], similarity=float(r[6])) for r in rows]


def source_info(source_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count(*), max(file_name), max(created_at) FROM chunks WHERE source_id = %s",
            (source_id,),
        ).fetchone()
    return {"source_id": source_id, "chunks": row[0], "file": row[1],
            "ingested_at": row[2].isoformat() if row[2] else None}
