"""generate(): the one way the app talks to any model. Adding a model is one provider file plus one line
in PROVIDERS. Policy, the same for every provider:
  1. try the chosen model;
  2. if it failed for a passing reason (per-minute limit, network, server error) try it once more;
  3. if it still fails, fall back to the local model (unless the content itself was refused);
  4. if the local model fails too, raise AllProvidersFailed listing every attempt.
Token counts come from the provider; missing ones are estimated with a tokenizer and labelled so."""

import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field

from . import gemini, groq, ollama, tokens
from .base import ProviderError

PROVIDERS = {"gemini": gemini, "groq": groq, "local": ollama}
LOCAL = "local"
# Shown on the answer badge: the small local model is weaker in Telugu and Hindi than Gemini.
LANGUAGE_NOTES = {LOCAL: "Small local model: Telugu and Hindi answers can be weaker than Gemini's."}
MAX_TRIES = 2               # first try + one retry, per provider
BACKOFF_SECONDS = 2.0       # wait before the retry when the provider gives no Retry-After
MAX_WAIT_SECONDS = 20.0     # a longer requested wait is not waited for; we fall back instead
sleep = time.sleep          # module-level so tests can skip real waits


@dataclass
class Generation:
    text: str
    model: str              # key actually used: gemini / groq / local
    model_id: str
    model_requested: str
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    response_ms: int        # time of the successful attempt
    attempts: list[dict] = field(default_factory=list)  # failed attempts before success
    fallback: dict | None = None

    @property
    def label(self) -> str:
        return f"{self.model}:{self.model_id}"

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


class AllProvidersFailed(ProviderError):
    code = "all_providers_failed"

    def __init__(self, attempts: list[dict]):
        tried = "; ".join(f"{a['model']} try {a['try']}: {a['code']} ({a['message']})" for a in attempts)
        super().__init__(f"Every model failed, so there is no answer. {tried}")
        self.attempts = attempts


def model_ids() -> dict[str, str]:
    return {key: p.model_id() for key, p in PROVIDERS.items()}


def stream(model: str, system: str, prompt: str) -> Iterator[dict]:
    """Yields {"text": piece} while writing, {"reset": {...}} if a model failed after writing some text
    (the partial text must be discarded), {"fallback": {...}} when switching to the local model,
    and finally {"done": Generation}."""
    if model not in PROVIDERS:
        raise ValueError(f"Unknown model '{model}'. Choose one of: {', '.join(PROVIDERS)}.")
    chain = [model] if model == LOCAL else [model, LOCAL]
    attempts: list[dict] = []
    fallback = None

    for position, key in enumerate(chain):
        provider = PROVIDERS[key]
        for try_number in range(1, MAX_TRIES + 1):
            text, usage = "", None
            start = time.perf_counter()
            try:
                for piece in provider.stream(system, prompt):
                    if "text" in piece:
                        text += piece["text"]
                        yield {"text": piece["text"]}
                    else:
                        usage = piece.get("usage")
            except ProviderError as exc:
                attempts.append({"model": key, "model_id": provider.model_id(), "try": try_number,
                                 "code": exc.code, "message": str(exc),
                                 "ms": round((time.perf_counter() - start) * 1000)})
                if text:
                    yield {"reset": {"model": key, "code": exc.code, "message": str(exc)}}
                if not exc.fallback:
                    raise
                wait = exc.retry_after if exc.retry_after is not None else BACKOFF_SECONDS
                if exc.retryable and try_number < MAX_TRIES and wait <= MAX_WAIT_SECONDS:
                    sleep(wait)
                    continue
                break  # not worth retrying (or retried already): move on to the next model
            else:
                yield {"done": _finish(key, provider, model, system, prompt, text, usage, start, attempts, fallback)}
                return
        if position + 1 < len(chain):
            last = attempts[-1]
            fallback = {"from": key, "to": chain[position + 1], "code": last["code"], "reason": last["message"]}
            yield {"fallback": fallback}
    raise AllProvidersFailed(attempts)


def _finish(key, provider, requested, system, prompt, text, usage, start, attempts, fallback) -> Generation:
    usage = usage or {}
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    estimated = input_tokens is None or output_tokens is None
    if input_tokens is None:
        input_tokens = tokens.estimate(system + "\n" + prompt)
    if output_tokens is None:
        output_tokens = tokens.estimate(text)
    return Generation(text=text.strip(), model=key, model_id=provider.model_id(), model_requested=requested,
                      input_tokens=input_tokens, output_tokens=output_tokens, tokens_estimated=estimated,
                      response_ms=round((time.perf_counter() - start) * 1000),
                      attempts=attempts, fallback=fallback)


def generate(model: str, system: str, prompt: str) -> Generation:
    """Non-streaming form: returns the text, token counts (actual or estimated) and response time."""
    for event in stream(model, system, prompt):
        if "done" in event:
            return event["done"]
    raise AssertionError("stream() ended without a result")
