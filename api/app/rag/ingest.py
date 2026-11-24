"""Ingestion for the three connectors: a PDF, a docs URL, or a GitHub repo. Each one is turned into text,
chunked, embedded with bge-m3 and stored in pgvector, tagged with the scope (which graph) and the source
node id. Re-ingesting the same source in the same scope replaces its old chunks."""

import tempfile
from pathlib import Path

from . import chunker, embeddings, pdf, repo, store, webpage
from .fetch import BlockedURL, FetchError, fetch

MAX_PAGE_BYTES = 10 * 1024 * 1024


class IngestError(ValueError):
    pass


def _store(source_id: str, source_type: str, file_name: str, chunks: list[tuple], scope: str,
           source_ref: int | None = None, on_commit=None) -> int:
    vectors = embeddings.embed([c[1] for c in chunks])
    return store.replace_source(source_id, source_type, file_name, chunks, vectors, scope, source_ref, on_commit)


def ingest_pdf(source_id: str, path: str, file_name: str, scope: str = store.INLINE, source_ref: int | None = None,
               on_commit=None) -> dict:
    pages = pdf.extract_pages(path)
    if not pages:
        raise IngestError(f"'{file_name}' has no extractable text. It may be a scanned PDF (OCR is not supported yet).")
    chunks = chunker.chunk_pages(pages)
    count = _store(source_id, "source_doc", file_name, chunks, scope, source_ref, on_commit)
    return {"source_id": source_id, "scope": scope, "kind": "pdf", "file": file_name, "pages": len(pages),
            "chunks": count}


def ingest_url(source_id: str, url: str, scope: str = store.INLINE, source_ref: int | None = None,
               on_commit=None) -> dict:
    """One docs page (HTML, plain text or a PDF link). Links on the page are not followed."""
    try:
        page = fetch(url, MAX_PAGE_BYTES)
    except BlockedURL:
        raise  # a refused private address is reported as such (HTTP 400), not as a broken page
    except FetchError as exc:
        raise IngestError(str(exc)) from exc
    if page.content_type == "application/pdf" or page.body.startswith(b"%PDF-"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.pdf"
            path.write_bytes(page.body)
            pages = pdf.extract_pages(str(path))
        chunks = [(p, text, page.url, page.url) for p, text in chunker.chunk_pages(pages)]
        title = page.url
    elif page.content_type in ("text/html", "application/xhtml+xml", "") or page.body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        title, text = webpage.html_to_text(page.body.decode("utf-8", errors="replace"))
        chunks = [(None, c, title or page.url, page.url) for c in chunker.chunk_text(text)]
    elif page.content_type.startswith("text/"):
        title = page.url
        chunks = [(None, c, page.url, page.url) for c in chunker.chunk_text(page.body.decode("utf-8", errors="replace"))]
    else:
        raise IngestError(f"{page.url} is '{page.content_type}', which is not a web page, text or PDF.")
    if not chunks:
        raise IngestError(f"{page.url} has no readable text.")
    count = _store(source_id, "source_doc", title or page.url, chunks, scope, source_ref, on_commit)
    return {"source_id": source_id, "scope": scope, "kind": "url", "url": page.url, "file": title or page.url,
            "chunks": count}


def ingest_repo(source_id: str, url: str, scope: str = store.INLINE, source_ref: int | None = None,
                on_commit=None) -> dict:
    """A GitHub repository: every text/code file, chunked per file; the path is part of each chunk so
    "which file does X" questions can match, and citations name and link the file."""
    try:
        name, files = repo.download(url)
    except FetchError as exc:
        raise IngestError(str(exc)) from exc
    chunks = [(None, f"File: {f.path}\n{c}", f.path, f.url) for f in files for c in chunker.chunk_text(f.text)]
    count = _store(source_id, "source_repo", name, chunks, scope, source_ref, on_commit)
    return {"source_id": source_id, "scope": scope, "kind": "repo", "repo": name, "files": len(files),
            "chunks": count}
