"""Splits text into sentences, shared by the chunker and the claim check so both agree on where a
sentence ends. A sentence ends at . ! ? or । followed by space, but not inside "Rs. 300", "Dr. Rao"
or "e.g. this": no split before a digit or a lowercase letter, and none after a common abbreviation."""

import re

SENTENCE_END = re.compile(r"(?<=[.!?।])\s+(?![\d(a-z])")
ABBREVIATIONS = {"rs", "dr", "mr", "mrs", "ms", "no", "st", "vs", "e.g", "i.e", "etc", "approx", "dept", "inc"}


def split(text: str) -> list[str]:
    out: list[str] = []
    for piece in (p.strip() for p in SENTENCE_END.split(text)):
        if not piece:
            continue
        last_word = out[-1].rsplit(" ", 1)[-1].rstrip(".").lower() if out else ""
        if last_word in ABBREVIATIONS:
            out[-1] = f"{out[-1]} {piece}"  # "Dr. Rao ..." is one sentence
        else:
            out.append(piece)
    return out
