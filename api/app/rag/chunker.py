"""Splits page text into overlapping chunks of about CHUNK_CHARS characters.
It breaks on paragraph, then sentence, then word boundaries, so a chunk rarely cuts a sentence in half.
The overlap is made of whole sentences: the next chunk repeats the last full sentence(s) of the previous
one, so a fact on a chunk boundary is complete in at least one chunk and never starts mid-sentence."""

import re

from . import sentences

CHUNK_CHARS = 500
OVERLAP_CHARS = 200  # at most this much is repeated, always as whole sentences


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
        for sentence in sentences.split(para):
            while len(sentence) > CHUNK_CHARS:  # one very long sentence: cut at a space
                cut = sentence.rfind(" ", 0, CHUNK_CHARS)
                cut = cut if cut > 0 else CHUNK_CHARS
                out.append(sentence[:cut])
                sentence = sentence[cut:].strip()
            if sentence:
                out.append(sentence)
    return out


def _overlap(chunk: str) -> str:
    """The last whole sentences of a chunk that fit in OVERLAP_CHARS (none if even the last one is longer)."""
    kept: list[str] = []
    for sentence in reversed(sentences.split(chunk)):
        if sum(len(s) + 1 for s in kept) + len(sentence) > OVERLAP_CHARS:
            break
        kept.insert(0, sentence)
    return " ".join(kept)


def chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text):
        if current and len(current) + 1 + len(piece) > CHUNK_CHARS:
            chunks.append(current)
            # Start the next chunk with the end of this one, so facts on a boundary stay findable.
            tail = _overlap(current)
            current = f"{tail} {piece}" if tail else piece
        else:
            current = f"{current} {piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Chunk each page on its own and keep its page number: [(page, chunk_text), ...]."""
    return [(page, chunk) for page, text in pages for chunk in chunk_text(text)]
