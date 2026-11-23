// Settings for the selected node. Agent: model (all four), language, local-only. Guardrails: which checks.
// Sources: upload a PDF, fetch a docs link or download a GitHub repo into THIS saved graph's documents
// (?graph_id=...), and see how many chunks it holds. MCP tool: server and read-only mode.
import { useEffect, useState } from "react";

const CHECKS = ["empty", "abuse", "off_topic"];

async function call(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.detail?.message || `HTTP ${res.status}`);
  return data;
}

function SourceSettings({ node, graphId, onConfig }) {
  const { type, config } = node.data;
  const kind = type === "source_repo" ? "repo" : "url" in config ? "url" : "pdf";
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [link, setLink] = useState(config.url || "");

  const query = graphId ? `?graph_id=${graphId}` : "";
  useEffect(() => {
    setInfo(null);
    if (!graphId) return;
    fetch(`/api/sources/${node.id}${query}`).then((r) => (r.ok ? r.json() : null)).then(setInfo).catch(() => {});
  }, [node.id, graphId, query]);

  async function run(work) {
    setBusy(true);
    setError("");
    try {
      const result = await work();
      setInfo(await call(`/api/sources/${node.id}${query}`));
      return result;
    } catch (err) {
      setError(err.message.includes("has no node")
        ? "Save the graph first, so this source is part of it, then add its content."
        : err.message);
    } finally {
      setBusy(false);
    }
  }

  function upload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    run(async () => {
      const r = await call(`/api/sources/${node.id}/pdf${query}`, { method: "POST", body: form });
      onConfig({ ...config, file: r.file });
    });
    e.target.value = "";
  }

  function fetchLink() {
    run(async () => {
      const r = await call(`/api/sources/${node.id}/${kind}${query}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: link }),
      });
      onConfig({ ...config, url: link });
      return r;
    });
  }

  if (!graphId) {
    return <p className="muted">Save the graph first: documents belong to a saved graph, so other graphs never see them.</p>;
  }
  return (
    <div className="cfg-source">
      {kind === "pdf" ? (
        <label className="cfg-field">PDF file <input type="file" accept="application/pdf,.pdf" onChange={upload} disabled={busy} /></label>
      ) : (
        <label className="cfg-field">
          {kind === "repo" ? "GitHub repository" : "Docs page link"}
          <span className="link-input">
            <input value={link} onChange={(e) => setLink(e.target.value)} aria-label="Source link"
              placeholder={kind === "repo" ? "https://github.com/owner/repo" : "https://docs.example.com/page"} />
            <button onClick={fetchLink} disabled={busy || !link}>{kind === "repo" ? "Download" : "Fetch"}</button>
          </span>
        </label>
      )}
      <p className="muted">
        {busy ? "reading and embedding…" : info?.chunks ? `${info.chunks} chunks stored (${info.file})` : "no content yet"}
      </p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default function ConfigPanel({ node, models, graphId, onConfig, onDelete }) {
  if (!node) return <aside className="cfg"><p className="muted">Select a node to see its settings.</p></aside>;
  const { type, config } = node.data;
  const set = (key, value) => onConfig({ ...config, [key]: value });

  return (
    <aside className="cfg">
      <h3>{node.id} <span className="muted">({type})</span></h3>
      {type === "agent" && (
        <>
          <label className="cfg-field">Model
            <select value={config.model || "gemini"} onChange={(e) => set("model", e.target.value)} aria-label="Agent model">
              {models.filter((m) => !config.local_only || m.local).map((m) => (
                <option key={m.key} value={m.key}>{m.label}{m.ready ? "" : " (not set up)"}</option>
              ))}
            </select>
          </label>
          <label className="cfg-field">Answer language
            <select value={config.language || "en"} onChange={(e) => set("language", e.target.value)} aria-label="Agent language">
              <option value="en">English</option>
              <option value="te">Telugu</option>
              <option value="hi">Hindi</option>
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={config.local_only === true} aria-label="Local only"
              onChange={(e) => onConfig({ ...config, local_only: e.target.checked,
                model: e.target.checked && !models.find((m) => m.key === config.model)?.local ? "local" : config.model })} />
            Local only (no cloud AI, not even as fallback)
          </label>
        </>
      )}
      {type === "guardrails" && CHECKS.map((c) => (
        <label key={c} className="check">
          <input type="checkbox" checked={(config.block || []).includes(c)}
            onChange={(e) => set("block", e.target.checked ? [...(config.block || []), c] : (config.block || []).filter((x) => x !== c))} />
          block {c.replace("_", "-")}
        </label>
      ))}
      {(type === "source_doc" || type === "source_repo") && (
        <SourceSettings key={node.id} node={node} graphId={graphId} onConfig={onConfig} />
      )}
      {type === "tool_mcp" && (
        <>
          <label className="cfg-field">Server <input value={config.server || ""} onChange={(e) => set("server", e.target.value)} /></label>
          <label className="cfg-field">Mode
            <select value={config.mode || ""} onChange={(e) => set("mode", e.target.value)}>
              <option value="read_only">read_only</option>
              <option value="read_write">read_write (needs allow_write)</option>
            </select>
          </label>
          <p className="muted">Tools are connected now and run in Phase 7.</p>
        </>
      )}
      {type === "tool_calculator" && <p className="muted">Exact arithmetic. Tools are connected now and run in Phase 7.</p>}
      {["input", "router", "output"].includes(type) && <p className="muted">No settings. This block is part of the fixed main line.</p>}
      {node.deletable !== false && <button className="danger" onClick={onDelete}>Remove node</button>}
    </aside>
  );
}
