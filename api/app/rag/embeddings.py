"""Turns text into 1024-number vectors with bge-m3 running in the ollama container.
The whole index uses this one multilingual model, so a Telugu question can match an English chunk."""

import httpx

from ..config import settings

EMBED_DIM = 1024
BATCH = 16


class EmbeddingError(RuntimeError):
    code = "embedding_unavailable"  # the pipeline's error event carries this code (Ollama down, model missing)


def embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        try:
            # num_gpu 0 keeps the small embedding model on the CPU, so it never pushes the chat model
            # (qwen3:14b) out of a laptop GPU's memory; that reload cost 10-60 s per question.
            r = httpx.post(f"{settings.ollama_url}/api/embed",
                           json={"model": settings.ollama_embed_model, "input": batch,
                                 "options": {"num_gpu": 0}}, timeout=120)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Cannot reach Ollama at {settings.ollama_url}: {exc}") from exc
        if r.status_code != 200:
            raise EmbeddingError(f"Ollama embedding failed ({r.status_code}): {r.text[:200]}. "
                                 f"Is '{settings.ollama_embed_model}' pulled?")
        got = r.json()["embeddings"]
        if len(got) != len(batch) or any(len(v) != EMBED_DIM for v in got):
            raise EmbeddingError(f"Expected {len(batch)} vectors of {EMBED_DIM} numbers from "
                                 f"{settings.ollama_embed_model}.")
        vectors.extend(got)
    return vectors


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
