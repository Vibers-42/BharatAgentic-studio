"""Text extraction for the document types besides PDF (Phase 8): plain text, Markdown, Word (.docx), CSV and
Excel (.xlsx). Rules, so nothing unsafe runs while reading a file:
- the type comes from the extension AND the content (a .docx/.xlsx must be a real zip, a .pdf must start %PDF-);
- zip-based files are refused if they would unpack to more than 50 MB or hold more than 2,000 entries;
- XML with a DTD or entity declarations is refused (no entity expansion attacks);
- spreadsheets are read with openpyxl in read-only, values-only mode: formulas are NOT calculated (their last
  saved value is used), macros, scripts and external links are never run or followed; at most 5 sheets,
  2,000 rows and 30 columns per sheet are indexed, and each row becomes one line "Header: value | ...";
- CSV: at most 5,000 rows and 30 columns, same line format; text files are read as UTF-8."""

import csv
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from . import chunker
from .ingest import _store

MAX_UNZIPPED = 50 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_ROWS_XLSX, MAX_SHEETS, MAX_COLS = 2000, 5, 30
MAX_ROWS_CSV = 5000
KINDS = {".pdf": "pdf", ".txt": "text", ".md": "markdown", ".markdown": "markdown", ".docx": "docx", ".csv": "csv",
         ".xlsx": "xlsx"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractError(ValueError):
    pass


def kind_for(file_name: str, data: bytes) -> str:
    ext = Path(file_name).suffix.lower()
    kind = KINDS.get(ext)
    if kind is None:
        raise ExtractError(f"'{file_name}' is not a supported file. Supported: PDF, TXT, Markdown, DOCX, CSV, XLSX.")
    if not data:
        raise ExtractError(f"'{file_name}' is empty.")
    if kind == "pdf" and not data.startswith(b"%PDF-"):
        raise ExtractError(f"'{file_name}' is not a PDF file.")
    if kind in ("docx", "xlsx") and not data.startswith(b"PK\x03\x04"):
        raise ExtractError(f"'{file_name}' is not a valid {ext[1:].upper()} file.")
    if kind in ("text", "markdown", "csv") and b"\x00" in data[:4096]:
        raise ExtractError(f"'{file_name}' looks like a binary file, not text.")
    return kind


def _safe_zip(path: Path) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ExtractError("The file is damaged (not a valid zip container).") from exc
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise ExtractError("The file unpacks to too much data; it was not read.")
    return z


def _xml(data: bytes) -> ElementTree.Element:
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ExtractError("The document contains XML declarations that are not allowed.")
    return ElementTree.fromstring(data)


def _docx(path: Path) -> str:
    z = _safe_zip(path)
    try:
        root = _xml(z.read("word/document.xml"))
    except KeyError as exc:
        raise ExtractError("This .docx has no document body.") from exc
    paragraphs = []
    for p in root.iter(f"{W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{W}t")).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _rows_to_text(rows: list[list[str]]) -> str:
    rows = [[c.strip() for c in r[:MAX_COLS]] for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    header, lines = rows[0], []
    for r in rows[1:]:
        cells = [f"{header[i] if i < len(header) and header[i] else f'Column {i + 1}'}: {v}" for i, v in enumerate(r) if v]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines) if lines else " | ".join(header)


def _csv(path: Path) -> str:
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [r for _, r in zip(range(MAX_ROWS_CSV + 1), reader)]
    return _rows_to_text(rows)


def _xlsx(path: Path) -> str:
    _safe_zip(path)  # size limits before openpyxl unpacks anything
    from openpyxl import load_workbook  # values only: formulas are not calculated, macros never run
    try:
        wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many kinds for broken files
        raise ExtractError(f"The spreadsheet could not be read: {exc}") from exc
    parts = []
    for ws in wb.worksheets[:MAX_SHEETS]:
        rows = []
        for row in ws.iter_rows(max_row=MAX_ROWS_XLSX + 1, max_col=MAX_COLS, values_only=True):
            rows.append(["" if v is None else str(v) for v in row])
        text = _rows_to_text(rows)
        if text:
            parts.append(f"Sheet: {ws.title}\n{text}")
    wb.close()
    return "\n\n".join(parts)


def extract_text(kind: str, path: Path) -> str:
    if kind in ("text", "markdown"):
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
    elif kind == "docx":
        text = _docx(path)
    elif kind == "csv":
        text = _csv(path)
    elif kind == "xlsx":
        text = _xlsx(path)
    else:
        raise ExtractError(f"No text extractor for {kind}.")
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    if not text:
        raise ExtractError("The file has no readable text.")
    return text


def ingest_document(kind: str, chunk_source_id: str, path: Path, file_name: str, scope: str, source_ref: int, hook) -> int:
    chunks = [(None, c) for c in chunker.chunk_text(extract_text(kind, path))]
    if not chunks:
        raise ExtractError("The file has no readable text.")
    return _store(chunk_source_id, "source_doc", file_name, chunks, scope, source_ref, hook)
