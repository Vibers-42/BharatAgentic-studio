"""Splits page text into overlapping chunks of about CHUNK_CHARS characters.
It breaks on paragraph, then sentence, then word boundaries, so a chunk rarely cuts a sentence in half."""

import re

CHUNK_CHARS = 500
OVERLAP_CHARS = 80

SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")  # "।" ends a sentence in Hindi


def _pieces(text: str) -> list[str]:
    """Paragraphs, with any paragraph longer than a chunk split into sentences, then words."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        if len(para) <= CHUNK_CHARS:
            out.append(para)
            continue
        for sentence in SENTENCE_END.split(para):
            while len(sentence) > CHUNK_CHARS:  # one very long sentence: cut at a space
                cut = sentence.rfind(" ", 0, CHUNK_CHARS)
                cut = cut if cut > 0 else CHUNK_CHARS
                out.append(sentence[:cut])
                sentence = sentence[cut:].strip()
            if sentence:
                out.append(sentence)
    return out


def chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text):
        if current and len(current) + 1 + len(piece) > CHUNK_CHARS:
            chunks.append(current)
            # Start the next chunk with the end of this one, so facts on a boundary stay findable.
            tail = current[-OVERLAP_CHARS:]
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            current = f"{tail} {piece}"
        else:
            current = f"{current} {piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Chunk each page on its own and keep its page number: [(page, chunk_text), ...]."""
    return [(page, chunk) for page, text in pages for chunk in chunk_text(text)]
