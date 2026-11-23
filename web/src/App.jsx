// Phase 1 home page: shows whether each container is reachable, using GET /api/status.
// The chat window, canvas and dashboard replace this in later phases.
import { useEffect, useState } from "react";

const SERVICES = [
  ["api", "API (FastAPI)"],
  ["db", "Database (Postgres + pgvector)"],
  ["ollama", "Local model (Ollama)"],
  ["mcp_runner", "MCP tool runner"],
];

export default function App() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus(await res.json());
    } catch {
      setStatus(null);
      setError("Cannot reach the API. Is `docker compose up` running?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function dot(ok) {
    if (loading) return "dot";
    return `dot ${ok ? "up" : "down"}`;
  }

  return (
    <main className="page">
      <h1>BharatAgent Studio</h1>
      <p className="sub">Phase 1: system status</p>

      {error && <p className="error">{error}</p>}

      <ul className="status">
        {SERVICES.map(([key, label]) => (
          <li key={key} data-service={key} data-ok={String(Boolean(status?.[key]))}>
            <span className={dot(status?.[key])} />
            {label}
            {key === "db" && status?.pgvector && <span className="note"> pgvector {status.pgvector}</span>}
          </li>
        ))}
        <li>
          <span className={dot(status?.keys?.gemini)} />
          Gemini API key set
        </li>
        <li>
          <span className={dot(status?.keys?.groq)} />
          Groq API key set
        </li>
      </ul>

      <button onClick={load} disabled={loading}>
        {loading ? "Checking…" : "Refresh"}
      </button>
    </main>
  );
}
