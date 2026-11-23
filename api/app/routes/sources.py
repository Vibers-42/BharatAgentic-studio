"""Source endpoints: upload a PDF for a source node (it is ingested straight away), see what a source
holds, or clear it. Files are saved in the uploads Docker volume, one folder per source id."""

import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import settings
from ..db import get_conn
from ..rag import store
from ..rag.embeddings import EmbeddingError
from ..rag.ingest import IngestError, ingest_pdf

router = APIRouter(prefix="/sources")

SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


def _check_source_id(source_id: str) -> None:
    if not SOURCE_ID.match(source_id):
        raise HTTPException(400, "Source id may only use letters, digits, '-' and '_' (max 64).")


def _safe_file_name(name: str | None) -> str:
    # Keep only the base name and plain characters, so a name like "../../x.pdf" cannot escape the folder.
    # Backslashes count as folder separators too, since Windows paths may arrive on this Linux server.
    base = SAFE_NAME.sub("_", Path((name or "upload.pdf").replace("\\", "/")).name).strip(" .") or "upload.pdf"
    if not base.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported in this phase.")
    return base


# A plain def (not async): FastAPI runs it in a worker thread, so slow embedding never blocks other requests.
@router.post("/{source_id}/pdf", status_code=201)
def upload_pdf(source_id: str, file: UploadFile = File(...)):
    _check_source_id(source_id)
    file_name = _safe_file_name(file.filename)
    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {settings.max_upload_mb} MB.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, f"'{file_name}' is not a PDF file.")

    folder = Path(settings.upload_dir) / source_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name
    path.write_bytes(data)

    try:
        return ingest_pdf(source_id, str(path), file_name)
    except IngestError as exc:
        raise HTTPException(422, str(exc))
    except EmbeddingError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:  # pypdf raises many types for broken files
        raise HTTPException(422, f"Could not read '{file_name}': {exc}")


@router.get("/{source_id}")
def get_source(source_id: str):
    _check_source_id(source_id)
    return store.source_info(source_id)


@router.delete("/{source_id}")
def clear_source(source_id: str):
    _check_source_id(source_id)
    with get_conn() as conn:
        deleted = conn.execute("DELETE FROM chunks WHERE source_id = %s", (source_id,)).rowcount
    return {"source_id": source_id, "deleted_chunks": deleted}
