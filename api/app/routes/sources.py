"""Source endpoints: add content to a source node (a PDF upload, a docs URL or a GitHub repo), see what a
source holds (or list every source of a graph), or clear it. ?graph_id=N stores the chunks for that saved graph only (the node must exist
there with the right type); without it they go to the "inline" scope used by graphs sent with a question.
Uploaded files are saved in the uploads Docker volume, one folder per scope and source id."""

import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import settings
from ..rag import store
from ..rag.embeddings import EmbeddingError
from ..rag.fetch import BlockedURL
from ..rag.ingest import IngestError, ingest_pdf, ingest_repo, ingest_url
from .graphs import load_graph

router = APIRouter(prefix="/sources")

SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


class LinkRequest(BaseModel):
    url: str | None = None  # optional with ?graph_id: then the node's config url is used


def _check_source_id(source_id: str) -> None:
    if not SOURCE_ID.match(source_id):
        raise HTTPException(400, "Source id may only use letters, digits, '-' and '_' (max 64).")


def _scope(source_id: str, graph_id: int | None, node_type: str | None = None) -> tuple[str, dict]:
    """The scope to store in, and the node's config when a saved graph is named."""
    _check_source_id(source_id)
    if graph_id is None:
        return store.INLINE, {}
    node = load_graph(graph_id).node(source_id)
    if node is None:
        raise HTTPException(404, f"Saved graph {graph_id} has no node '{source_id}'.")
    if node_type and node.type != node_type:
        raise HTTPException(400, f"Node '{source_id}' in graph {graph_id} is a {node.type}, not a {node_type}.")
    return store.graph_scope(graph_id), node.config


def _safe_file_name(name: str | None) -> str:
    # Keep only the base name and plain characters, so a name like "../../x.pdf" cannot escape the folder.
    # Backslashes count as folder separators too, since Windows paths may arrive on this Linux server.
    base = SAFE_NAME.sub("_", Path((name or "upload.pdf").replace("\\", "/")).name).strip(" .") or "upload.pdf"
    if not base.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files can be uploaded; add web pages with POST /sources/{id}/url.")
    return base


def _run(ingest, *args):
    try:
        return ingest(*args)
    except BlockedURL as exc:
        raise HTTPException(400, str(exc))
    except IngestError as exc:
        raise HTTPException(422, str(exc))
    except EmbeddingError as exc:
        raise HTTPException(503, str(exc))


# Plain defs (not async): FastAPI runs them in a worker thread, so slow downloads and embedding never
# block other requests.
@router.post("/{source_id}/pdf", status_code=201)
def upload_pdf(source_id: str, file: UploadFile = File(...), graph_id: int | None = None):
    scope, _ = _scope(source_id, graph_id, "source_doc")
    file_name = _safe_file_name(file.filename)
    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {settings.max_upload_mb} MB.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, f"'{file_name}' is not a PDF file.")

    folder = Path(settings.upload_dir) / scope.replace(":", "_") / source_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name
    path.write_bytes(data)
    try:
        return _run(ingest_pdf, source_id, str(path), file_name, scope)
    except HTTPException:
        raise
    except Exception as exc:  # pypdf raises many types for broken files
        raise HTTPException(422, f"Could not read '{file_name}': {exc}")


@router.post("/{source_id}/url", status_code=201)
def add_url(source_id: str, req: LinkRequest, graph_id: int | None = None):
    scope, config = _scope(source_id, graph_id, "source_doc")
    url = req.url or config.get("url")
    if not url:
        raise HTTPException(400, "Send the page as {\"url\": \"https://...\"}.")
    return _run(ingest_url, source_id, url, scope)


@router.post("/{source_id}/repo", status_code=201)
def add_repo(source_id: str, req: LinkRequest, graph_id: int | None = None):
    scope, config = _scope(source_id, graph_id, "source_repo")
    url = req.url or config.get("url")
    if not url:
        raise HTTPException(400, "Send the repository as {\"url\": \"https://github.com/owner/repo\"}.")
    return _run(ingest_repo, source_id, url, scope)


@router.get("")
def list_sources(graph_id: int | None = None):
    """What every source holds: in a saved graph's documents with ?graph_id=N, else in the inline scope."""
    scope = store.INLINE
    if graph_id is not None:
        load_graph(graph_id)  # 404 if the graph does not exist
        scope = store.graph_scope(graph_id)
    return {"scope": scope, "sources": store.list_sources(scope)}


@router.get("/{source_id}")
def get_source(source_id: str, graph_id: int | None = None):
    scope, _ = _scope(source_id, graph_id)
    return store.source_info(source_id, scope)


@router.delete("/{source_id}")
def clear_source(source_id: str, graph_id: int | None = None):
    scope, _ = _scope(source_id, graph_id)
    return {"source_id": source_id, "scope": scope, "deleted_chunks": store.clear_source(source_id, scope)}
