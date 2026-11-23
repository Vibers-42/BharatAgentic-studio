"""Local provider: qwen2.5:3b (or OLLAMA_CHAT_MODEL) running in the ollama container. No API key and
no API fee, but it uses this computer's CPU/GPU and time. Streams /api/chat; Ollama reports token
counts in its final message (prompt_eval_count, eval_count)."""

import json
from collections.abc import Iterator

import httpx

from ..config import settings
from .base import ProviderError, http_error, network_error

KEY = "local"
PROVIDER = "Ollama"


def model_id() -> str:
    return settings.ollama_chat_model


def configured() -> bool:
    return True  # no key needed; whether the model is pulled is checked by /models


def stream(system: str, prompt: str) -> Iterator[dict]:
    body = {
        "model": settings.ollama_chat_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "stream": True,
        "options": {"temperature": 0.2},
    }
    usage = None
    try:
        # A small model on a laptop CPU can take a while, so the timeout is generous.
        with httpx.stream("POST", f"{settings.ollama_url}/api/chat", json=body, timeout=300) as r:
            if r.status_code != 200:
                detail = r.read().decode("utf-8", "replace")[:300]
                if r.status_code == 404:
                    raise ProviderError(f"The local model '{settings.ollama_chat_model}' is not pulled. "
                                        f"Run: docker compose exec ollama ollama pull {settings.ollama_chat_model}",
                                        code="model_not_found")
                raise http_error(PROVIDER, r.status_code, detail)
            for line in r.iter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise ProviderError(f"Ollama error: {data['error']}", code="model_error")
                text = (data.get("message") or {}).get("content")
                if text:
                    yield {"text": text}
                if data.get("done") and "eval_count" in data:
                    # prompt_eval_count can be missing when Ollama reuses a cached prompt.
                    usage = {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data["eval_count"]}
    except httpx.HTTPError as exc:
        raise network_error(PROVIDER, settings.ollama_url, exc) from exc
    yield {"usage": usage}
