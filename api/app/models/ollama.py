"""Local models in the ollama container: no API key and no API fee, but they use this computer's CPU/GPU
and time. Two are configured: the main local model (OLLAMA_CHAT_MODEL, qwen3:14b) and a small backup
(OLLAMA_BACKUP_MODEL, qwen2.5:3b) that still answers when the big one cannot load.
Streams /api/chat; Ollama reports token counts in its final message (prompt_eval_count, eval_count)."""

import json
from collections.abc import Callable, Iterator

import httpx

from ..config import settings
from .base import ProviderError, http_error, network_error

PROVIDER = "Ollama"
# Models that "think out loud" before answering; thinking is turned off so only the answer streams.
THINKING_MODELS = ("qwen3", "deepseek-r1", "gpt-oss", "magistral")
def _drop_thinking(text: str, state: dict) -> str:
    """Remove <think>...</think> from streamed text; state["inside"] carries over between pieces."""
    out = ""
    while text:
        if state["inside"]:
            end = text.find("</think>")
            if end < 0:
                return out
            text, state["inside"] = text[end + len("</think>"):].lstrip(), False
        else:
            start = text.find("<think>")
            if start < 0:
                return out + text
            out, text, state["inside"] = out + text[:start], text[start + len("<think>"):], True
    return out


def stream(system: str, prompt: str, model: str | None = None) -> Iterator[dict]:
    model = model or settings.ollama_chat_model
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "stream": True,
        "options": {"temperature": 0.2, "num_predict": settings.ollama_num_predict},
    }
    if model.startswith(THINKING_MODELS):
        body["think"] = False
    usage = None
    think = {"inside": False}
    try:
        # A local model on a laptop can take a while (loading a 9 GB model alone takes seconds), so the
        # timeout is generous.
        with httpx.stream("POST", f"{settings.ollama_url}/api/chat", json=body, timeout=600) as r:
            if r.status_code != 200:
                detail = r.read().decode("utf-8", "replace")[:300]
                if r.status_code == 404:
                    raise ProviderError(f"The local model '{model}' is not pulled. "
                                        f"Run: docker compose exec ollama ollama pull {model}",
                                        code="model_not_found")
                if "memory" in detail.lower():
                    raise ProviderError(f"Not enough memory to load the local model '{model}': {detail}",
                                        code="out_of_memory")
                raise http_error(PROVIDER, r.status_code, detail)
            for line in r.iter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise ProviderError(f"Ollama error for '{model}': {data['error']}", code="model_error")
                # If a model still emits <think>...</think>, never stream that part as the answer.
                text = _drop_thinking((data.get("message") or {}).get("content") or "", think)
                if text:
                    yield {"text": text}
                if data.get("done") and "eval_count" in data:
                    # prompt_eval_count can be missing when Ollama reuses a cached prompt.
                    usage = {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data["eval_count"]}
    except httpx.HTTPError as exc:
        raise network_error(PROVIDER, settings.ollama_url, exc) from exc
    yield {"usage": usage}


class LocalModel:
    """One configured Ollama model, with the same interface as the gemini and groq provider modules."""

    PROVIDER = PROVIDER

    def __init__(self, key: str, model_id: Callable[[], str]):
        self.KEY = key
        self._model_id = model_id

    def model_id(self) -> str:
        return self._model_id()

    def configured(self) -> bool:
        return True  # no key needed; whether the model is pulled is checked by /models

    def stream(self, system: str, prompt: str) -> Iterator[dict]:
        # Looked up on the module at call time, so tests can replace ollama.stream.
        return globals()["stream"](system, prompt, model=self.model_id())


main = LocalModel("local", lambda: settings.ollama_chat_model)
backup = LocalModel("local_small", lambda: settings.ollama_backup_model)


# Kept for callers that treat this module itself as the main local provider.
KEY = main.KEY


def model_id() -> str:
    return main.model_id()


def configured() -> bool:
    return True
