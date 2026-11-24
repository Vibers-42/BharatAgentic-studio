// System status: whether each container is reachable (GET /api/status), which models are ready
// (GET /api/models) and which read-only tools the MCP runner offers (GET /api/tools).
import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import Icon from "./Icon.jsx";

const SERVICES = [
  ["api", "API", "FastAPI pipeline, sources and usage", "builder"],
  ["db", "Database", "Postgres with pgvector", "knowledge"],
  ["ollama", "Local models", "Ollama: Qwen 3 14B, Qwen 2.5 3B, bge-m3", "cpu"],
  ["mcp_runner", "MCP tool runner", "Isolated read-only tools, no database access", "mcp"],
];

export default function Status() {
  const [status, setStatus] = useState(null);
  const [models, setModels] = useState(null);
  const [tools, setTools] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const [s, m, t] = await Promise.allSettled([api("/status"), api("/models"), api("/tools")]);
    setStatus(s.status === "fulfilled" ? s.value : null);
    setModels(m.status === "fulfilled" ? m.value : null);
    setTools(t.status === "fulfilled" ? t.value : null);
    if (s.status === "rejected") setError("Cannot reach the API. Is `docker compose up` running?");
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <section className="page-pad">
      <header className="page-head">
        <div>
          <h1>System status</h1>
          <p className="muted">Everything runs in five local containers: web, api, db, mcp-runner and ollama.</p>
        </div>
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? <span className="spinner" /> : <Icon name="refresh" size={15} />} Refresh
        </button>
      </header>

      {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}

      <div className="card-grid four">
        {SERVICES.map(([key, label, about, icon]) => {
          const ok = status?.[key];
          return (
            <div key={key} className={`service card${loading ? " skeleton-soft" : ""}`} data-service={key} data-ok={String(Boolean(ok))}>
              <div className="service-head">
                <span className="chip-icon"><Icon name={icon} size={16} /></span>
                <span className={`pill ${loading ? "idle" : ok ? "ok" : "err"}`}>
                  <span className={`dot ${loading ? "" : ok ? "up" : "down"}`} /> {loading ? "Checking" : ok ? "Healthy" : "Down"}
                </span>
              </div>
              <strong>{label}</strong>
              <span className="muted small">{about}{key === "db" && status?.pgvector ? ` · pgvector ${status.pgvector}` : ""}</span>
            </div>
          );
        })}
      </div>

      <div className="grid2">
        <div className="card">
          <div className="card-head"><h3>Models</h3>{models?.local_only && <span className="pill ok"><Icon name="lock" size={12} /> Server is local-only</span>}</div>
          {!models ? <div className="skeleton-line" /> : (
            <ul className="model-list">
              {models.models.map((m) => (
                <li key={m.key}>
                  <span className="chip-icon"><Icon name={m.local ? "cpu" : "cloud"} size={15} /></span>
                  <span className="model-text">
                    <strong>{m.label}</strong>
                    <span className="muted small mono">{m.provider} · {m.model_id}</span>
                    {!m.ready && <span className="field-hint">{m.why_not_ready}</span>}
                  </span>
                  <span className="model-price muted small">{m.local ? "no API fee" : m.priced ? `$${m.input_per_million_usd} / $${m.output_per_million_usd} per 1M` : "no published price"}</span>
                  <span className={`pill ${m.ready ? "ok" : "warn"}`}>{m.ready ? "Ready" : "Not set up"}</span>
                </li>
              ))}
            </ul>
          )}
          {models && <p className="muted small">If the chosen model fails, the answer falls back to {models.fallback?.join?.(" → ") || "the local model"}. Prices checked on {models.prices_checked_on}.</p>}
        </div>
        <div className="card">
          <div className="card-head"><h3>Tools</h3></div>
          <ul className="model-list">
            <li>
              <span className="chip-icon"><Icon name="calculator" size={15} /></span>
              <span className="model-text"><strong>Calculator</strong><span className="muted small">{tools?.calculator?.operations || "safe arithmetic"}</span></span>
              <span className="pill ok">Ready</span>
            </li>
            {(tools?.mcp_servers || []).map((s) => (
              <li key={s.name}>
                <span className="chip-icon"><Icon name="mcp" size={15} /></span>
                <span className="model-text">
                  <strong>MCP tool · {s.name}</strong>
                  <span className="muted small">{s.ok ? s.tools.map((t) => `${t.name}${t.read_only ? "" : " (writes, blocked)"}`).join(", ") : s.error}</span>
                </span>
                <span className={`pill ${s.ok ? "ok" : "err"}`}>{s.ok ? `${s.tools.filter((t) => t.read_only).length} read-only` : "Down"}</span>
              </li>
            ))}
            {!tools && <div className="skeleton-line" />}
          </ul>
          <p className="muted small">Gemini key {status?.keys?.gemini ? "set" : "missing"} · Groq key {status?.keys?.groq ? "set" : "missing"} (keys stay in .env, never in the browser).</p>
        </div>
      </div>
    </section>
  );
}
