"""Checks that each cited sentence is backed by the chunk it cites, not just that a citation exists.
Two tests per sentence:
  1. numbers: every number in the sentence (amounts, days, times, percentages) must appear in a cited
     chunk. Script-independent, so it works for Telugu and Hindi answers about English documents.
  2. meaning: the sentence's bge-m3 embedding must be close enough to a cited chunk's embedding
     (multilingual, so a Telugu sentence can match an English chunk).
A sentence that fails either test is removed from the answer; a sentence with no citation is removed
too. If nothing is left, the agent says it does not know."""

import re

import numpy as np

from . import embeddings, sentences, store
from .citations import MARKER

# Measured with bge-m3 on samples/refund-policy.pdf: correct sentences scored 0.69-0.83 (English),
# 0.70 (Hindi) and 0.57 (Telugu) against the chunk they cite; off-topic and unrelated sentences 0.28-0.37.
# A wrong number still scores high (0.67), which is why the numbers test exists.
CLAIM_MIN_SIMILARITY = 0.45

DIGIT_MAP = str.maketrans("०१२३४५६७८९౦౧౨౩౪౫౬౭౮౯", "01234567890123456789")  # Devanagari, Telugu digits
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers(text: str) -> set[str]:
    """Numbers as plain digits: '3,500' -> '3500', '9:30' -> {'9', '30'}, '०५' -> '05' -> '5'."""
    found = set()
    for n in NUMBER.findall(text.translate(DIGIT_MAP)):
        n = n.replace(",", "")
        if "." in n and n.replace(".", "").isdigit() and n.count(".") == 1:
            whole, frac = n.split(".")
            found.add(str(int(whole)) if frac.strip("0") == "" else f"{int(whole)}.{frac}")
        else:
            found.add(str(int(n)) if n.isdigit() else n)
    return found


LEADING_MARKERS = re.compile(r"^((?:\[\d+(?:\s*,\s*\d+)*\]\s*)+)")


def split_sentences(answer: str) -> list[str]:
    """Split into sentences. Citation markers right after a full stop ("fact. [1] Next.") belong to the
    sentence before them, so the citation is not lost or moved to the wrong sentence."""
    out: list[str] = []
    for piece in sentences.split(answer):
        lead = LEADING_MARKERS.match(piece)
        if lead and out:
            out[-1] = f"{out[-1]} {lead.group(1).strip()}"
            piece = piece[lead.end():].strip()
        if piece:
            out.append(piece)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))


def verify(answer: str, chunks: list[store.Chunk]) -> dict:
    """Return {"answer": kept sentences, "claims": [one verdict per sentence], "removed": count}.
    chunks are the numbered sources the model saw: [1] is chunks[0]."""
    parts = split_sentences(answer)
    plain = [MARKER.sub("", s).strip() for s in parts]
    cites = [sorted({int(n) for m in MARKER.findall(s) for n in m.split(",") if 1 <= int(n) <= len(chunks)})
             for s in parts]
    to_embed = [i for i, p in enumerate(plain) if cites[i] and any(ch.isalnum() for ch in p)]
    sentence_vectors = dict(zip(to_embed, embeddings.embed([plain[i] for i in to_embed]))) if to_embed else {}
    chunk_vectors = store.chunk_vectors([chunks[n - 1].id for i in to_embed for n in cites[i]])

    claims, kept = [], []
    for i, sentence in enumerate(parts):
        text = plain[i]
        if not any(ch.isalnum() for ch in text):
            continue  # stray punctuation or a bare marker
        verdict, reason, similarity = "supported", None, None
        if not cites[i]:
            verdict, reason = "uncited", "no citation"
        else:
            cited = [chunks[n - 1] for n in cites[i]]
            source_numbers = set().union(*(numbers(c.content) for c in cited))
            missing = sorted(numbers(text) - source_numbers)
            vectors = [chunk_vectors[c.id] for c in cited if c.id in chunk_vectors]
            similarity = round(max(_cosine(sentence_vectors[i], v) for v in vectors), 3) if vectors else None
            if missing:
                verdict, reason = "unsupported", f"number(s) {', '.join(missing)} not in the cited source"
            elif similarity is not None and similarity < CLAIM_MIN_SIMILARITY:
                verdict, reason = "unsupported", f"meaning too far from the cited source ({similarity})"
        claims.append({"sentence": text, "cites": cites[i], "verdict": verdict, "reason": reason,
                       "similarity": similarity})
        if verdict == "supported":
            kept.append(sentence)
    return {"answer": " ".join(kept), "claims": claims,
            "removed": sum(c["verdict"] != "supported" for c in claims)}
