"""Ingestion for a PDF source: extract text per page, chunk, embed with bge-m3, store in pgvector
tagged with the source node id. Re-ingesting the same source replaces its old chunks."""

from . import chunker, embeddings, pdf, store


class IngestError(ValueError):
    pass


def ingest_pdf(source_id: str, path: str, file_name: str) -> dict:
    pages = pdf.extract_pages(path)
    if not pages:
        raise IngestError(f"'{file_name}' has no extractable text. It may be a scanned PDF (OCR is not supported yet).")
    chunks = chunker.chunk_pages(pages)
    vectors = embeddings.embed([text for _, text in chunks])
    count = store.replace_source(source_id, "source_doc", file_name, chunks, vectors)
    return {"source_id": source_id, "file": file_name, "pages": len(pages), "chunks": count}
