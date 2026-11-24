"""The project knowledge registry. A source is one PDF, docs page or GitHub repository (and, where supported,
a text, Markdown, Word, CSV or Excel file) added once to a project; every agent and every member of that project
can then use it. Ingestion runs inside the request (synchronously), and the status says honestly what happened:

  pending -> processing -> ready     (chunks and vectors stored, in the same transaction as "ready")
                        -> failed    (the error is kept; a new source keeps no chunks and no file)

Re-indexing a ready source that then fails keeps its previous, working content (status stays "ready" and
`error` explains the last attempt). Files live in uploads/project_<id>/source_<id>/ and are removed when the
source is replaced or deleted. Chunks use scope "project:<id>" and source id "src<id>", and point at the source
row (foreign key with ON DELETE CASCADE), so deleting a source removes its chunks and its agent links."""

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException

from .config import settings
from .db import get_conn
from .rag import extract
from .rag.embeddings import EmbeddingError
from .rag.fetch import BlockedURL
from .rag.ingest import IngestError, ingest_pdf, ingest_repo, ingest_url

SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")
COLUMNS = ["id", "project_id", "kind", "title", "original_name", "url", "storage_path", "size_bytes", "content_hash",
           "revision", "status", "chunk_count", "error", "created_at", "updated_at", "uploaded_by",
           "uploaded_by_name", "used_by_agents"]


def scope_of(project_id: int) -> str:
    return f"project:{project_id}"


def chunk_source_id(source_id: int) -> str:
    return f"src{source_id}"


def safe_file_name(name: str | None) -> str:
    """Base name only, plain characters: '../../x.pdf' or 'C:\\x.pdf' cannot leave the source folder."""
    base = SAFE_NAME.sub("_", Path((name or "upload").replace("\\", "/")).name).strip(" .")
    return base[:120] or "upload"


def _row_to_dict(row) -> dict:
    d = dict(zip(COLUMNS, row))
    for k in ("created_at", "updated_at"):
        d[k] = d[k].isoformat() if d[k] else None
    return d


SELECT = """SELECT s.id, s.project_id, s.kind, s.title, s.original_name, s.url, s.storage_path, s.size_bytes,
                   s.content_hash, s.revision, s.status, s.chunk_count, s.error, s.created_at, s.updated_at,
                   s.uploaded_by, coalesce(u.full_name, u.email, 'imported'),
                   (SELECT count(*) FROM agent_sources a WHERE a.source_id = s.id)
            FROM sources s LEFT JOIN users u ON u.id = s.uploaded_by"""


def list_sources(project_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(SELECT + " WHERE s.project_id = %s ORDER BY s.updated_at DESC", (project_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_source(source_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(SELECT + " WHERE s.id = %s", (source_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such source.")
    d = _row_to_dict(row)
    with get_conn() as conn:
        d["agents"] = [{"id": r[0], "name": r[1], "node_id": r[2]} for r in conn.execute(
            "SELECT g.id, g.name, a.node_id FROM agent_sources a JOIN graphs g ON g.id = a.graph_id "
            "WHERE a.source_id = %s ORDER BY g.name", (source_id,)).fetchall()]
    return d


def create_source(project_id: int, user_id: int, kind: str, title: str, *, original_name: str | None = None,
                  url: str | None = None) -> int:
    with get_conn() as conn:
        return conn.execute(
            "INSERT INTO sources (project_id, uploaded_by, kind, title, original_name, url, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING id",
            (project_id, user_id, kind, title[:300], original_name, url)).fetchone()[0]


def _set(source_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = %s" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE sources SET {cols}, updated_at = now() WHERE id = %s", (*fields.values(), source_id))


def _status(source_id: int) -> tuple[str, int]:
    with get_conn() as conn:
        return conn.execute("SELECT status, chunk_count FROM sources WHERE id = %s", (source_id,)).fetchone()


def _ready_hook(source_id: int, **fields):
    """Marks the source ready inside the chunk-writing transaction, so "ready" and the chunks commit together."""
    def hook(conn, count: int):
        extra = "".join(f", {k} = %s" for k in fields)
        conn.execute(f"UPDATE sources SET status = 'ready', chunk_count = %s, error = NULL, updated_at = now(){extra} "
                     "WHERE id = %s", (count, *fields.values(), source_id))
    return hook


def _fail(source_id: int, was_ready: bool, message: str) -> None:
    if was_ready:  # the previous content is still there and still valid
        _set(source_id, error=f"Last update failed: {message}"[:1000])
    else:
        _set(source_id, status="failed", error=message[:1000], chunk_count=0)


def _run(source_id: int, work) -> dict:
    """Run one ingestion with honest status changes and clear HTTP errors."""
    was_ready = _status(source_id)[0] == "ready"
    _set(source_id, status="processing" if not was_ready else "ready")
    try:
        work()
    except BlockedURL as exc:
        _fail(source_id, was_ready, str(exc))
        raise HTTPException(400, str(exc))
    except (IngestError, extract.ExtractError) as exc:
        _fail(source_id, was_ready, str(exc))
        raise HTTPException(422, str(exc))
    except EmbeddingError as exc:
        _fail(source_id, was_ready, f"The embedding model is not available: {exc}")
        raise HTTPException(503, str(exc))
    except HTTPException:
        raise
    except Exception as exc:  # pypdf and the document parsers raise many kinds of errors for broken files
        _fail(source_id, was_ready, f"Could not read the file: {exc}")
        raise HTTPException(422, f"Could not read the file: {exc}")
    return get_source(source_id)


def ingest_file(source_id: int, project_id: int, file_name: str, data: bytes) -> dict:
    """Store the file in the source's folder and index it. On success any older file of this source is removed;
    on failure the new file is removed (and an older, still indexed file is kept)."""
    kind = extract.kind_for(file_name, data)
    folder = Path(settings.upload_dir) / f"project_{project_id}" / f"source_{source_id}"
    folder.mkdir(parents=True, exist_ok=True)
    # unique per upload: two replacements of one source at the same moment must not share (and move) one file
    tmp = folder / f".incoming-{uuid.uuid4().hex}-{file_name}"
    tmp.write_bytes(data)
    final_rel = f"project_{project_id}/source_{source_id}/{file_name}"
    old = _old_storage(source_id)

    def work():
        sha = hashlib.sha256(data).hexdigest()
        hook = _ready_hook(source_id, kind=kind, title=file_name, original_name=file_name, size_bytes=len(data),
                           content_hash=sha, storage_path=final_rel)
        if kind == "pdf":
            ingest_pdf(chunk_source_id(source_id), str(tmp), file_name, scope_of(project_id), source_id, hook)
        else:
            extract.ingest_document(kind, chunk_source_id(source_id), tmp, file_name, scope_of(project_id), source_id, hook)

    try:
        result = _run(source_id, work)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, folder / file_name)
    if old and old != final_rel:
        _remove_file(old)
    return result


def ingest_link(source_id: int, project_id: int, kind: str, url: str) -> dict:
    def work():
        if kind == "repo":
            holder = {}

            def hook(conn, count):
                _ready_hook(source_id)(conn, count)
            r = ingest_repo(chunk_source_id(source_id), url, scope_of(project_id), source_id, hook)
            holder.update(r)
            name = r.get("repo", url)
            _set(source_id, title=name, revision=name.split("@", 1)[1] if "@" in name else None, url=url)
        else:
            r = ingest_url(chunk_source_id(source_id), url, scope_of(project_id), source_id, _ready_hook(source_id))
            _set(source_id, title=r.get("file") or url, url=r.get("url") or url)
    return _run(source_id, work)


def reindex(source: dict) -> dict:
    if source["kind"] in ("url", "repo"):
        return ingest_link(source["id"], source["project_id"], source["kind"], source["url"])
    if not source.get("storage_path"):
        raise HTTPException(400, "This source has no stored file to read again; upload the file again.")
    path = _resolve(source["storage_path"])
    if not path.is_file():
        raise HTTPException(410, "The stored file is missing; upload the file again.")
    return ingest_file(source["id"], source["project_id"], path.name, path.read_bytes())


def delete_source(source_id: int) -> None:
    """Chunks (with their vectors) and agent links go with the row (ON DELETE CASCADE); then the folder."""
    with get_conn() as conn:
        row = conn.execute("DELETE FROM sources WHERE id = %s RETURNING project_id", (source_id,)).fetchone()
    if row:
        shutil.rmtree(Path(settings.upload_dir) / f"project_{row[0]}" / f"source_{source_id}", ignore_errors=True)


def delete_project_files(project_id: int) -> None:
    shutil.rmtree(Path(settings.upload_dir) / f"project_{project_id}", ignore_errors=True)


def _old_storage(source_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT storage_path FROM sources WHERE id = %s", (source_id,)).fetchone()
    return row[0] if row else None


def _resolve(rel: str) -> Path:
    """A stored path, refused if it would point outside the uploads folder."""
    root = Path(settings.upload_dir).resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Invalid stored path.")
    return path


def _remove_file(rel: str) -> None:
    try:
        _resolve(rel).unlink(missing_ok=True)
    except HTTPException:
        pass


def file_path(source: dict) -> Path:
    if not source.get("storage_path"):
        raise HTTPException(404, "This source has no stored file.")
    path = _resolve(source["storage_path"])
    if not path.is_file():
        raise HTTPException(404, "The stored file is missing.")
    return path


def ready_sources(project_id: int, ids: list[int] | None = None) -> dict[int, dict]:
    """Ready sources of this project (optionally only these ids): {id: {title, kind}}."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, kind FROM sources WHERE project_id = %s AND status = 'ready' AND chunk_count > 0 "
            "AND (%s::int[] IS NULL OR id = ANY(%s))", (project_id, ids, ids)).fetchall()
    return {r[0]: {"title": r[1], "kind": r[2]} for r in rows}
