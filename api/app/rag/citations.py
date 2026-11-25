"""Checks the model's citations against the chunks it was actually given.
Markers that point at no real chunk are removed, and an answer with no valid citation is not trusted."""

import re

from .prompt import DONT_KNOW, ROMAN_DONT_KNOW
from .store import Chunk

MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")  # [2] or [1, 3]
# Some models write citations with full-width or lenticular brackets: 【1】 ［1］ 〔1〕
WIDE_MARKER = re.compile(r"[【［〔]\s*(\d+(?:\s*[,，]\s*\d+)*)\s*[】］〕]")


def normalize_markers(answer: str) -> str:
    """Turn 【1】-style citations into [1], so every model's citations are read the same way."""
    return WIDE_MARKER.sub(lambda m: "[" + m.group(1).replace("，", ",") + "]", answer)


def is_dont_know(answer: str) -> bool:
    text = " ".join(answer.split()).lower()
    return any(s.lower().rstrip(".।") in text for s in [*DONT_KNOW.values(), *ROMAN_DONT_KNOW.values()])


def check(answer: str, chunks: list[Chunk]) -> dict:
    """Return the cleaned answer, the citations in order of first use, and the invalid markers removed."""
    used: list[int] = []
    invalid: list[int] = []

    def fix(match: re.Match) -> str:
        valid = []
        for n in (int(x) for x in match.group(1).split(",")):
            if 1 <= n <= len(chunks):
                valid.append(n)
                if n not in used:
                    used.append(n)
            else:
                invalid.append(n)
        return "".join(f"[{n}]" for n in valid)

    cleaned = MARKER.sub(fix, normalize_markers(answer)).strip()
    if not any(ch.isalnum() for ch in MARKER.sub("", cleaned)):
        used = []  # only markers, no words: nothing is being claimed, so nothing is supported
    citations = [
        {"n": n, "source_id": c.source_id, "file": c.file_name, "page": c.page, "chunk": c.chunk_index,
         "similarity": round(c.similarity, 3), "snippet": c.content[:240], "origin": c.origin}
        for n in used for c in [chunks[n - 1]]
    ]
    return {"answer": cleaned, "citations": citations, "invalid_markers": invalid}
