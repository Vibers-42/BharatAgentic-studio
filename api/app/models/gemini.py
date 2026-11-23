"""Gemini API client that streams the answer as it is generated (streamGenerateContent with SSE).
Yields {"text": ...} pieces, then one {"usage": {...}} with the token counts Gemini reports.
Phase 4 wraps this in the shared generate() function with retry and local fallback."""

import json
from collections.abc import Iterator

import httpx

from ..config import settings


class GeminiError(RuntimeError):
    pass


def _body(system: str, prompt: str) -> dict:
    config: dict = {"temperature": 0.2}
    if "2.5-flash" in settings.gemini_model:
        config["thinkingConfig"] = {"thinkingBudget": 0}  # no hidden thinking: faster first token
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": config,
    }


def stream(system: str, prompt: str) -> Iterator[dict]:
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set in .env, so Gemini cannot answer. "
                          "Add the key and restart the api container.")
    url = (f"{settings.gemini_base_url}/v1beta/models/{settings.gemini_model}"
           ":streamGenerateContent?alt=sse")
    # The key goes in a header, never in the URL, so it cannot end up in logs.
    headers = {"x-goog-api-key": settings.gemini_api_key}
    usage = {"input_tokens": 0, "output_tokens": 0}
    finish = None

    with httpx.stream("POST", url, headers=headers, json=_body(system, prompt), timeout=60) as r:
        if r.status_code != 200:
            detail = r.read().decode("utf-8", "replace")[:300]
            hint = " (free-tier limit reached?)" if r.status_code == 429 else ""
            raise GeminiError(f"Gemini returned HTTP {r.status_code}{hint}: {detail}")
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            for cand in data.get("candidates", [])[:1]:
                finish = cand.get("finishReason") or finish
                for part in cand.get("content", {}).get("parts", []):
                    if part.get("text"):
                        yield {"text": part["text"]}
            meta = data.get("usageMetadata")
            if meta:
                usage = {"input_tokens": meta.get("promptTokenCount", 0),
                         "output_tokens": meta.get("candidatesTokenCount", 0)}

    if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
        raise GeminiError(f"Gemini stopped the answer for safety reasons ({finish}).")
    yield {"usage": usage, "finish_reason": finish}
