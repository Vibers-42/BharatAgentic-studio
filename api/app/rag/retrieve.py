"""Finds the chunks that can answer a question, from the sources connected to the agent only.
Chunks below the similarity threshold are dropped, so weak matches never reach the model."""

from ..config import settings
from . import embeddings, store


def retrieve(question: str, source_ids: list[str]) -> tuple[list[store.Chunk], list[store.Chunk]]:
    """Return (relevant chunks, all top-k candidates). Candidates are kept for the event detail,
    so the UI and tests can see why something was judged not relevant."""
    if not source_ids:
        return [], []
    candidates = store.search(embeddings.embed_one(question), source_ids, settings.top_k)
    relevant = [c for c in candidates if c.similarity >= settings.min_similarity]
    return relevant, candidates
