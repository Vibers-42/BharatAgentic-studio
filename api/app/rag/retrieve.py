"""Finds the chunks that can answer a question, from the sources connected to the agent only.
Chunks below the similarity threshold are dropped, so weak matches never reach the model."""

from ..config import settings
from . import embeddings, store


BROAD_K = 10          # candidates looked at for a broad question ("what is this project about?")
BROAD_MARGIN = 0.08   # extra chunks may be this much below the threshold, only once one chunk clears it
BROAD_MAX = 8         # bounded context: at most this many chunks for a broad question


def retrieve(question: str, source_ids: list[str], scope: str = store.INLINE,
             broad: bool = False) -> tuple[list[store.Chunk], list[store.Chunk]]:
    """Return (relevant chunks, all top-k candidates) from the given sources in this scope. Candidates are
    kept for the event detail, so the UI and tests can see why something was judged not relevant.

    broad: a question about a whole document needs several sections. More candidates are looked at, and once
    the best one clears the threshold (so the sources really are about this), chunks slightly below it are
    added too, at most BROAD_MAX. If nothing clears the threshold the answer is still "I don't know"."""
    if not source_ids:
        return [], []
    k = BROAD_K if broad else settings.top_k
    candidates = store.search(embeddings.embed_one(question), source_ids, k, scope)
    relevant = [c for c in candidates if c.similarity >= settings.min_similarity]
    if broad and relevant:
        relevant = [c for c in candidates if c.similarity >= settings.min_similarity - BROAD_MARGIN][:BROAD_MAX]
    return relevant, candidates
