"""Gemini API client that streams the answer as it is generated (streamGenerateContent with SSE).
Yields {"text": ...} pieces, then one {"usage": {...}} with the token counts Gemini reports.
A per-minute rate limit (HTTP 429) is retried a few times; a daily quota is reported at once.
Phase 4 wraps this in the shared generate() function with local fallback."""

import json
import re
import time
from collections.abc import Iterator

import httpx

from ..config import settings

MAX_RETRIES = 2          # extra attempts after a per-minute 429, never more
MAX_WAIT_SECONDS = 20.0  # longest single wait; a longer Retry-After is reported instead of waited for
BACKOFF_SECONDS = 2.0    # first wait when Gemini gives no retry hint (then 4, 8, ...)
sleep = time.sleep       # a module-level name, so tests can skip the waits


class GeminiError(RuntimeError):
    code = "model_error"


class GeminiQuotaError(GeminiError):
    """HTTP 429. kind is "daily" (no retry helps today) or "rate" (per-minute, retried)."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None, attempts: int = 1):
        super().__init__(message)
        self.kind = kind
        self.code = f"quota_{kind}"
        self.retry_after = retry_after
        self.attempts = attempts


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


def _quota_message(kind: str, quota_id: str, retry_after: float | None, attempts: int) -> str:
    model = settings.gemini_model
    if kind == "daily":
        return (f"Gemini daily quota for {model} is used up (HTTP 429, {quota_id}). "
                "It resets at midnight Pacific time; no retry was made.")
    wait = f" Gemini asked to wait {retry_after:g}s." if retry_after else ""
    return (f"Gemini rate limit for {model} was hit (HTTP 429, {quota_id}) and still applied after "
            f"{attempts} attempt(s).{wait} Wait a minute and try again.")


def _open(client: httpx.Client, url: str, headers: dict, body: dict) -> httpx.Response:
    """Send the request; on a per-minute 429 wait (Retry-After, else exponential backoff) and retry,
    at most MAX_RETRIES times. A daily 429, or a wait longer than MAX_WAIT_SECONDS, is raised at once."""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            r = client.send(client.build_request("POST", url, headers=headers, json=body), stream=True)
        except httpx.HTTPError as exc:  # network trouble is a model error, never a quota error
            raise GeminiError(f"Cannot reach Gemini at {settings.gemini_base_url}: {type(exc).__name__}: {exc}") from exc
        if r.status_code != 429:
            return r
        text = r.read().decode("utf-8", "replace")
        r.close()
        kind, retry_after, quota_id = classify_429(r.headers, text)
        wait = retry_after if retry_after is not None else BACKOFF_SECONDS * 2 ** (attempt - 1)
        if kind == "daily" or attempt > MAX_RETRIES or wait > MAX_WAIT_SECONDS:
            raise GeminiQuotaError(kind, _quota_message(kind, quota_id, retry_after, attempt), retry_after, attempt)
        sleep(wait)
    raise AssertionError("unreachable")


def stream(system: str, prompt: str) -> Iterator[dict]:
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set in .env, so Gemini cannot answer. "
                          "Add the key and restart the api container.")
    url = (f"{settings.gemini_base_url}/v1beta/models/{settings.gemini_model}"
           ":streamGenerateContent?alt=sse")
    # The key goes in a header, never in the URL or an error message, so it cannot end up in logs.
    headers = {"x-goog-api-key": settings.gemini_api_key}
    usage = {"input_tokens": 0, "output_tokens": 0}
    finish = None

    with httpx.Client(timeout=60) as client:
        r = _open(client, url, headers, _body(system, prompt))
        try:
            if r.status_code != 200:
                detail = r.read().decode("utf-8", "replace")[:300].replace(settings.gemini_api_key, "<key>")
                raise GeminiError(f"Gemini returned HTTP {r.status_code}: {detail}")
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
        finally:
            r.close()

    if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
        raise GeminiError(f"Gemini stopped the answer for safety reasons ({finish}).")
    yield {"usage": usage, "finish_reason": finish}
