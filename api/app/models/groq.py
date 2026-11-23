"""Groq provider: the OpenAI-compatible chat API with streaming. Yields {"text": ...} pieces, then one
{"usage": {...}} from the final chunk (stream_options.include_usage), or {"usage": None} if Groq sent none.
It makes exactly one attempt and classifies any failure; retry and fallback live in generate.py."""

import json
from collections.abc import Iterator

import httpx

from ..config import settings
from .base import ProviderError, QuotaError, http_error, network_error

KEY = "groq"
PROVIDER = "Groq"


def model_id() -> str:
    return settings.groq_model


def configured() -> bool:
    return bool(settings.groq_api_key)


def _quota_error(r: httpx.Response, body: str) -> QuotaError:
    """Groq names the limit in the message, e.g. "Limit 1000, Used 1000 ... requests per day (RPD)"."""
    try:
        message = json.loads(body).get("error", {}).get("message", "")
    except ValueError:
        message = body
    retry_after = None
    if r.headers.get("retry-after", "").replace(".", "", 1).isdigit():
        retry_after = float(r.headers["retry-after"])
    daily = "per day" in message.lower()
    kind = "daily" if daily else "rate"
    text = (f"Groq daily limit for {settings.groq_model} is used up (HTTP 429)." if daily
            else f"Groq rate limit for {settings.groq_model} was hit (HTTP 429).")
    return QuotaError(kind, f"{text} {message[:200]}".strip(), retry_after)


def stream(system: str, prompt: str) -> Iterator[dict]:
    if not settings.groq_api_key:
        raise ProviderError("GROQ_API_KEY is not set in .env, so Groq cannot answer.", code="no_key")
    url = f"{settings.groq_base_url}/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    body = {
        "model": settings.groq_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    usage = None
    try:
        with httpx.stream("POST", url, headers=headers, json=body, timeout=60) as r:
            if r.status_code == 429:
                raise _quota_error(r, r.read().decode("utf-8", "replace"))
            if r.status_code != 200:
                detail = r.read().decode("utf-8", "replace")[:300].replace(settings.groq_api_key, "<key>")
                raise http_error(PROVIDER, r.status_code, detail)
            for line in r.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                data = json.loads(line[6:])
                for choice in data.get("choices", [])[:1]:
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield {"text": text}
                reported = data.get("usage") or (data.get("x_groq") or {}).get("usage")
                if reported and "completion_tokens" in reported:
                    usage = {"input_tokens": reported.get("prompt_tokens", 0),
                             "output_tokens": reported.get("completion_tokens", 0)}
    except httpx.HTTPError as exc:
        raise network_error(PROVIDER, settings.groq_base_url, exc) from exc
    yield {"usage": usage}
