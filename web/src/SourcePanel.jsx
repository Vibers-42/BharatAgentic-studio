// The three source connectors: upload a PDF, add a GitHub repo, add a docs link. Each one shows what it
// holds and has its own "connected to agent" box (the stand-in for drawing a line, until the canvas).
import { useEffect, useState } from "react";
import { SOURCES } from "./graph.js";

async function postJson(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

function describe(info) {
  if (!info?.chunks) return "empty";
  if (info.source_type === "source_repo") return `${info.file}: ${info.files} files, ${info.chunks} chunks`;
  return `${info.file}: ${info.chunks} chunks`;
}

export default function SourcePanel({ state, setState }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [links, setLinks] = useState({ chat_repo: "", chat_url: "" });

  useEffect(() => {
    for (const s of SOURCES) {
      fetch(`/api/sources/${s.id}`)
        .then((r) => r.json())
        .then((info) => setState((st) => ({ ...st, [s.id]: { ...st[s.id], info } })))
        .catch(() => {});
    }
  }, [setState]);

  async function run(id, work) {
    setBusy(id);
    setError("");
    try {
      const result = await work();
      const info = await (await fetch(`/api/sources/${id}`)).json();
      setState((st) => ({ ...st, [id]: { ...st[id], info, config: result.url ? { url: result.url } : st[id]?.config } }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  function uploadPdf(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    run("chat_pdf", async () => {
      const res = await fetch("/api/sources/chat_pdf/pdf", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Upload failed (HTTP ${res.status})`);
      setState((st) => ({ ...st, chat_pdf: { ...st.chat_pdf, config: { file: data.file } } }));
      return data;
    });
    e.target.value = "";
  }

  const addLink = (id, kind) => run(id, () => postJson(`/api/sources/${id}/${kind}`, { url: links[id] }));

  return (
    <div className="card">
      <h2>Sources</h2>
      {SOURCES.map((s) => (
        <div key={s.id} className="source-row">
          <strong>{s.label}</strong>
          {s.kind === "pdf" ? (
            <input type="file" accept="application/pdf,.pdf" onChange={uploadPdf} disabled={busy === s.id} />
          ) : (
            <span className="link-input">
              <input
                value={links[s.id]}
                onChange={(e) => setLinks((l) => ({ ...l, [s.id]: e.target.value }))}
                placeholder={s.kind === "repo" ? "https://github.com/owner/repo" : "https://docs.example.com/page"}
                aria-label={s.label}
              />
              <button onClick={() => addLink(s.id, s.kind)} disabled={busy === s.id || !links[s.id]}>Add</button>
            </span>
          )}
          <span className="muted">{busy === s.id ? "reading and embedding…" : describe(state[s.id]?.info)}</span>
          {state[s.id]?.info?.chunks > 0 && (
            <label className="check">
              <input
                type="checkbox"
                checked={state[s.id]?.connected ?? true}
                onChange={(e) => setState((st) => ({ ...st, [s.id]: { ...st[s.id], connected: e.target.checked } }))}
              />
              Connected to agent
            </label>
          )}
        </div>
      ))}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
