"""A stand-in for the Gemini API that speaks the same streamGenerateContent SSE format, sending its
answer in several pieces with a pause between them. Used by tests, and by verify_phase3.ps1 to prove
token streaming reaches the browser through nginx. It never calls Google.

Run:  python tests/fake_gemini.py 8765 [pause_seconds]   (then point GEMINI_BASE_URL at http://<host>:8765)
A pause of 0 sends every piece at once, which is how a buffering proxy would look to the client.

Answers: with sources it repeats the first sentence of source [1] and cites [1]; if the question
contains FAKE_BAD_CITATION it cites [9] instead; with no sources it sends a short greeting."""

import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAUSE_SECONDS = 0.25


def reply_for(system: str, prompt: str) -> list[str]:
    if "Sources:" not in prompt:
        return ["Hello! ", "Connect a source ", "to get cited answers."]
    first = re.search(r"\[1\] \([^)]*\)\n(.+?)(?:\.|$)", prompt, re.S)
    fact = " ".join(first.group(1).split())[:160] if first else "the source says so"
    marker = "[9]" if "FAKE_BAD_CITATION" in prompt else "[1]"
    words = f"According to the document, {fact}. {marker}".split(" ")
    return [" ".join(words[i:i + 4]) + " " for i in range(0, len(words), 4)]


REQUESTS: dict[str, int] = {}  # requests seen per API key, so tests can count retries


def _google_429(quota_id: str, retry_delay: str) -> dict:
    """The shape of a real Gemini 429 body: QuotaFailure says which quota, RetryInfo how long to wait."""
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.",
                      "details": [
                          {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                           "violations": [{"quotaId": quota_id, "quotaValue": "10"}]},
                          {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]}}


def quota_error(key: str, request_number: int) -> dict | None:
    """Special keys simulate quota problems:
    quota-daily          every request: daily quota used up
    quota-rate-N         the first N requests: per-minute limit, then normal answers
    quota-rate-forever   every request: per-minute limit
    quota-exceeded       every request: a bare 429 with no details"""
    if key == "quota-daily":
        return _google_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "30s")
    if key == "quota-rate-forever":
        return _google_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "0.05s")
    if key.startswith("quota-rate-") and request_number <= int(key.rsplit("-", 1)[1]):
        return _google_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "0.05s")
    if key == "quota-exceeded":
        return {"error": {"code": 429, "message": "Resource exhausted"}}
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not self.headers.get("x-goog-api-key") or "key=" in self.path:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "missing x-goog-api-key header"}')
            return
        key = self.headers.get("x-goog-api-key")
        REQUESTS[key] = REQUESTS.get(key, 0) + 1
        quota = quota_error(key, REQUESTS[key])
        if quota:
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(quota).encode())
            return
        system = body["systemInstruction"]["parts"][0]["text"]
        prompt = body["contents"][0]["parts"][0]["text"]
        pieces = reply_for(system, prompt)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i, piece in enumerate(pieces):
            event = {"candidates": [{"content": {"parts": [{"text": piece}], "role": "model"}}]}
            if i == len(pieces) - 1:
                event["candidates"][0]["finishReason"] = "STOP"
                event["usageMetadata"] = {"promptTokenCount": len(prompt) // 4,
                                          "candidatesTokenCount": len("".join(pieces)) // 4}
            self.wfile.write(f"data: {json.dumps(event)}\r\n\r\n".encode())
            self.wfile.flush()
            time.sleep(PAUSE_SECONDS)


def start(port: int = 0) -> ThreadingHTTPServer:
    """Start in a background thread; port 0 picks a free port (see server.server_port)."""
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if len(sys.argv) > 2:
        PAUSE_SECONDS = float(sys.argv[2])
    print(f"fake Gemini listening on :{port}, pause {PAUSE_SECONDS}s", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
