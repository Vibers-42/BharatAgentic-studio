// Calls POST /api/pipeline/run and hands every Server-Sent Event to onEvent as it arrives.
// EventSource only supports GET, so this reads the response stream with fetch instead. The session cookie goes
// along automatically (same origin) and the CSRF header comes from api.js.
import { headers } from "./api.js";

export async function runPipeline(body, onEvent, signal) {
  const res = await fetch("/api/pipeline/run", {
    method: "POST",
    credentials: "same-origin",
    headers: headers("POST", true),
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401) window.dispatchEvent(new CustomEvent("session-ended"));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const errors = data?.detail?.errors?.map((e) => e.message).join(" ") || "";
    throw new Error(`${data?.detail?.message || data?.detail || `HTTP ${res.status}`} ${errors}`.trim());
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = block.split("\n").find((line) => line.startsWith("data: "));
      if (data) onEvent(JSON.parse(data.slice(6)));
    }
  }
}
