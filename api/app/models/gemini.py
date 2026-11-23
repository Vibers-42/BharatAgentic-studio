"""Gemini provider: streams the answer as it is generated (streamGenerateContent with SSE).
Yields {"text": ...} pieces, then one {"usage": {...}} with the token counts Gemini reports.
It makes exactly one attempt and classifies any failure; retry and fallback live in generate.py."""

import json
import re
from collections.abc import Iterator

import httpx

from ..config import settings
from .base import ProviderError, QuotaError, http_error, network_error

KEY = "gemini"
PROVIDER = "Gemini"


def model_id() -> str:
    return settings.gemini_model


def configured() -> bool:
    return bool(settings.gemini_api_key)


# Kept as names so existing callers and tests read naturally.
GeminiError = ProviderError
GeminiQuotaError = QuotaError


def _body(system: str, prompt: str) -> dict:
    config: dict = {"temperature": 0.2}
    if "2.5-flash" in settings.gemini_model:
        config["thinkingConfig"] = {"thinkingBudget": 0}  # no hidden thinking: faster first token
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": config,
    }


def classify_429(headers: httpx.Headers, body: str) -> tuple[str, float | None, str]:
    """Read Google's error details: which quota was hit (per day or per minute) and how long to wait.
    Returns (kind, retry_after_seconds, quota_id)."""
    retry_after = None
    if headers.get("retry-after", "").replace(".", "", 1).isdigit():
        retry_after = float(headers["retry-after"])
    quota_ids: list[str] = []
    try:
        details = json.loads(body).get("error", {}).get("details", [])
    except ValueError:
        details = []
    for d in details:
        if d.get("@type", "").endswith("QuotaFailure"):
            quota_ids += [v.get("quotaId", "") for v in d.get("violations", [])]
        if d.get("@type", "").endswith("RetryInfo") and retry_after is None:
            m = re.match(r"([\d.]+)s", d.get("retryDelay", ""))
            retry_after = float(m.group(1)) if m else None
    daily = any("PerDay" in q for q in quota_ids)
    return ("daily" if daily else "rate"), retry_after, ",".join(quota_ids) or "unknown"


def _quota_error(kind: str, quota_id: str, retry_after: float | None) -> QuotaError:
    if kind == "daily":
        msg = (f"Gemini daily quota for {settings.gemini_model} is used up (HTTP 429, {quota_id}). "
               "It resets at midnight Pacific time.")
    else:
        wait = f" Gemini asked to wait {retry_after:g}s." if retry_after else ""
        msg = f"Gemini rate limit for {settings.gemini_model} was hit (HTTP 429, {quota_id}).{wait}"
    return QuotaError(kind, msg, retry_after)


def stream(system: str, prompt: str) -> Iterator[dict]:
    if not settings.gemini_api_key:
        raise ProviderError("GEMINI_API_KEY is not set in .env, so Gemini cannot answer.", code="no_key")
    url = (f"{settings.gemini_base_url}/v1beta/models/{settings.gemini_model}"
           ":streamGenerateContent?alt=sse")
    # The key goes in a header, never in the URL or an error message, so it cannot end up in logs.
    headers = {"x-goog-api-key": settings.gemini_api_key}
    usage = None
    finish = None
    try:
        with httpx.stream("POST", url, headers=headers, json=_body(system, prompt), timeout=60) as r:
            if r.status_code == 429:
                kind, retry_after, quota_id = classify_429(r.headers, r.read().decode("utf-8", "replace"))
                raise _quota_error(kind, quota_id, retry_after)
            if r.status_code != 200:
                detail = r.read().decode("utf-8", "replace")[:300].replace(settings.gemini_api_key, "<key>")
                raise http_error(PROVIDER, r.status_code, detail)
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
                if meta and "candidatesTokenCount" in meta:
                    usage = {"input_tokens": meta.get("promptTokenCount", 0),
                             "output_tokens": meta.get("candidatesTokenCount", 0)}
    except httpx.HTTPError as exc:
        raise network_error(PROVIDER, settings.gemini_base_url, exc) from exc

    if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
        # A refused answer is not handed to another model: that would get around the refusal.
        raise ProviderError(f"Gemini stopped the answer for safety reasons ({finish}).",
                            code="safety", fallback=False)
    yield {"usage": usage}
