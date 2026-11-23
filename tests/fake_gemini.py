"""Stand-ins for the model APIs, used by tests and the verify scripts. They never call Google or Groq.
  POST /v1beta/models/<m>:streamGenerateContent   Gemini's SSE format (x-goog-api-key header)
  POST /openai/v1/chat/completions                 Groq's OpenAI-compatible SSE format (Bearer key)
  POST /api/chat                                   Ollama's line-by-line JSON format
Each answer is sent in several pieces with a pause between them, so streaming can be timed.

Run:  python tests/fake_gemini.py 8765 [pause_seconds]
      then point GEMINI_BASE_URL / GROQ_BASE_URL (and, in tests, OLLAMA_URL) at http://<host>:8765
A pause of 0 sends every piece at once, which is how a buffering proxy would look to the client.

Answers: with sources it repeats the first sentence of source [1] and cites [1]; if the question
contains FAKE_BAD_CITATION it cites [9] instead; FAKE_WRONG_NUMBER makes it claim a number the source
does not contain; FAKE_UNCITED adds a sentence without a citation. With no sources it sends a greeting.

Faults, triggered by a word in the question (Gemini and Groq only):
  FAULT_500        HTTP 500 server error, every time
  FAULT_DAILY      HTTP 429, daily quota used up
  FAULT_RATE_ONCE  HTTP 429 per-minute limit on the first request for this question, then normal
  FAULT_MIDSTREAM  two pieces, then the connection drops before the answer is complete
  FAULT_NO_USAGE   a normal answer but no token counts, so the api must estimate them
Gemini API keys can also simulate quota problems: quota-daily, quota-rate-N, quota-rate-forever,
quota-exceeded (see quota_error)."""

import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAUSE_SECONDS = 0.25
REQUESTS: dict[str, int] = {}   # requests seen per API key / per (provider, question), to count retries


def reply_for(prompt: str) -> list[str]:
    if "Sources:" not in prompt:
        return ["Hello! ", "Connect a source ", "to get cited answers."]
    # Quote the first real sentence of source [1] (5+ words), not a heading like "4." or "1. Fees".
    block = re.search(r"\[1\] \([^)]*\)\n(.+?)(?:\n\n\[2\]|\n\nQuestion:|$)", prompt, re.S)
    sentences = re.split(r"(?<=[.!?])\s+(?![\d(a-z])", " ".join(block.group(1).split())) if block else []
    fact = next((s.rstrip(".") for s in sentences if len(s.split()) >= 5), "the source says so")[:160]
    marker = "[9]" if "FAKE_BAD_CITATION" in prompt else "[1]"
    text = f"According to the document, {fact}. {marker}"
    if "FAKE_WRONG_NUMBER" in prompt:  # a claim with a number the source does not contain
        text = f"According to the document, the fee is Rs. 98765 for everything {marker}."
    if "FAKE_UNCITED" in prompt:       # an extra sentence with no citation at all
        text += " This policy is the most generous in the country."
    words = text.split(" ")
    return [" ".join(words[i:i + 4]) + " " for i in range(0, len(words), 4)]


def _google_429(quota_id: str, retry_delay: str) -> dict:
    """The shape of a real Gemini 429 body: QuotaFailure says which quota, RetryInfo how long to wait."""
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.",
                      "details": [
                          {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                           "violations": [{"quotaId": quota_id, "quotaValue": "10"}]},
                          {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]}}


DAILY = _google_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "30s")
PER_MINUTE = _google_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "0.05s")


def quota_error(key: str, request_number: int) -> dict | None:
    """Special Gemini keys simulate quota problems:
    quota-daily          every request: daily quota used up
    quota-rate-N         the first N requests: per-minute limit, then normal answers
    quota-rate-forever   every request: per-minute limit
    quota-exceeded       every request: a bare 429 with no details"""
    if key == "quota-daily":
        return DAILY
    if key == "quota-rate-forever":
        return PER_MINUTE
    if key.startswith("quota-rate-") and request_number <= int(key.rsplit("-", 1)[1]):
        return PER_MINUTE
    if key == "quota-exceeded":
        return {"error": {"code": 429, "message": "Resource exhausted"}}
    return None


def fault_for(provider: str, prompt: str) -> str | None:
    """Which fault word, if any, the question carries. FAULT_RATE_ONCE only fires on the first request.
    Requests are counted per provider and fault word, so GET /_requests can prove how often a client retried."""
    for word in ("FAULT_500", "FAULT_DAILY", "FAULT_MIDSTREAM", "FAULT_NO_USAGE", "FAULT_RATE_ONCE"):
        if word in prompt:
            REQUESTS[f"{provider}:{word}"] = REQUESTS.get(f"{provider}:{word}", 0) + 1
    for word in ("FAULT_500", "FAULT_DAILY", "FAULT_MIDSTREAM", "FAULT_NO_USAGE"):
        if word in prompt:
            return word
    if "FAULT_RATE_ONCE" in prompt:
        key = f"{provider}:{prompt}"
        REQUESTS[key] = REQUESTS.get(key, 0) + 1
        return "FAULT_RATE_ONCE" if REQUESTS[key] == 1 else None
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _json(self, status: int, body: dict, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _stream(self, lines: list[bytes], content_type: str, drop_after: int | None = None):
        """Send lines with a pause between them. drop_after: stop after that many lines and close the
        connection although the declared length promised more, as a dropped connection would."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if drop_after is not None:
            self.send_header("Content-Length", "1000000")
        self.end_headers()
        for i, line in enumerate(lines):
            if drop_after is not None and i == drop_after:
                self.close_connection = True
                return
            self.wfile.write(line)
            self.wfile.flush()
            time.sleep(PAUSE_SECONDS)

    def do_GET(self):
        if self.path == "/_requests":  # how many requests each fault received, for the verify scripts
            return self._json(200, REQUESTS)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "streamGenerateContent" in self.path:
            self._gemini(body)
        elif self.path.endswith("/openai/v1/chat/completions"):
            self._groq(body)
        elif self.path == "/api/chat":
            self._ollama(body)
        else:
            self._json(404, {"error": f"unknown path {self.path}"})

    def _gemini(self, body: dict):
        if not self.headers.get("x-goog-api-key") or "key=" in self.path:
            return self._json(401, {"error": "missing x-goog-api-key header"})
        key = self.headers.get("x-goog-api-key")
        REQUESTS[key] = REQUESTS.get(key, 0) + 1
        quota = quota_error(key, REQUESTS[key])
        if quota:
            return self._json(429, quota)
        prompt = body["contents"][0]["parts"][0]["text"]
        fault = fault_for("gemini", prompt)
        if fault == "FAULT_500":
            return self._json(500, {"error": {"code": 500, "message": "Internal error (simulated)"}})
        if fault == "FAULT_DAILY":
            return self._json(429, DAILY)
        if fault == "FAULT_RATE_ONCE":
            return self._json(429, PER_MINUTE)
        pieces = reply_for(prompt)
        lines = []
        for i, piece in enumerate(pieces):
            event = {"candidates": [{"content": {"parts": [{"text": piece}], "role": "model"}}]}
            if i == len(pieces) - 1:
                event["candidates"][0]["finishReason"] = "STOP"
                if fault != "FAULT_NO_USAGE":
                    event["usageMetadata"] = {"promptTokenCount": len(prompt) // 4,
                                              "candidatesTokenCount": len("".join(pieces)) // 4}
            lines.append(f"data: {json.dumps(event)}\r\n\r\n".encode())
        self._stream(lines, "text/event-stream", drop_after=2 if fault == "FAULT_MIDSTREAM" else None)

    def _groq(self, body: dict):
        if not self.headers.get("Authorization", "").startswith("Bearer ") or self.headers["Authorization"] == "Bearer ":
            return self._json(401, {"error": {"message": "Invalid API Key"}})
        prompt = body["messages"][-1]["content"]
        fault = fault_for("groq", prompt)
        if fault == "FAULT_500":
            return self._json(500, {"error": {"message": "Internal Server Error (simulated)"}})
        if fault == "FAULT_DAILY":
            return self._json(429, {"error": {"message": "Rate limit reached for model on requests per day (RPD): "
                                              "Limit 1000, Used 1000"}}, {"retry-after": "3600"})
        if fault == "FAULT_RATE_ONCE":
            return self._json(429, {"error": {"message": "Rate limit reached on requests per minute (RPM)"}},
                              {"retry-after": "0.05"})
        pieces = reply_for(prompt)
        lines = [f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}\n\n".encode() for p in pieces]
        if fault != "FAULT_NO_USAGE":
            usage = {"prompt_tokens": len(prompt) // 4, "completion_tokens": len("".join(pieces)) // 4}
            lines.append(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode())
        lines.append(b"data: [DONE]\n\n")
        self._stream(lines, "text/event-stream", drop_after=2 if fault == "FAULT_MIDSTREAM" else None)

    def _ollama(self, body: dict):
        if body.get("model") == "missing-model":
            return self._json(404, {"error": f"model '{body['model']}' not found"})
        prompt = body["messages"][-1]["content"]
        pieces = reply_for(prompt)
        lines = [json.dumps({"message": {"role": "assistant", "content": p}, "done": False}).encode() + b"\n"
                 for p in pieces]
        final = {"message": {"role": "assistant", "content": ""}, "done": True,
                 "eval_count": len("".join(pieces)) // 4}
        if "NO_PROMPT_COUNT" not in prompt:
            final["prompt_eval_count"] = len(prompt) // 4
        lines.append(json.dumps(final).encode() + b"\n")
        self._stream(lines, "application/x-ndjson")


def start(port: int = 0) -> ThreadingHTTPServer:
    """Start in a background thread; port 0 picks a free port (see server.server_port)."""
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if len(sys.argv) > 2:
        PAUSE_SECONDS = float(sys.argv[2])
    print(f"fake model APIs listening on :{port}, pause {PAUSE_SECONDS}s", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
