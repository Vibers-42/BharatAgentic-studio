// Knowledge: every document source in one place. Pick the quick chat's sources or a saved agent, see what
// each source holds (from the api, so uploads made anywhere show up), upload PDFs with progress, fetch docs
// links, download GitHub repos, or clear a source. A saved agent's documents are visible to that agent only.
import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { SOURCES } from "./graph.js";
import Icon from "./Icon.jsx";
import SourceCard from "./SourceCard.jsx";

export default function Knowledge({ go }) {
  const [agents, setAgents] = useState([]);
  const [scope, setScope] = useState("");     // "" = quick chat, else a graph id
  const [graph, setGraph] = useState(null);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => { api("/graphs").then(setAgents).catch(() => {}); }, []);

  const refresh = useCallback(() => {
    const q = scope ? `?graph_id=${scope}` : "";
    return api(`/sources${q}`)
      .then((d) => { setInfo(Object.fromEntries(d.sources.map((s) => [s.source_id, s]))); setError(""); })
      .catch((err) => setError(err.message));
  }, [scope]);

  useEffect(() => {
    setInfo(null);
    setGraph(null);
    refresh();
    if (scope) api(`/graphs/${scope}`).then(setGraph).catch((err) => setError(err.message));
  }, [scope, refresh]);

  const cards = scope
    ? (graph?.nodes || []).filter((n) => n.type === "source_doc" || n.type === "source_repo").map((n) => ({
      id: n.id, kind: n.type === "source_repo" ? "repo" : "url" in n.config ? "url" : "pdf", url: n.config.url || "",
    }))
    : SOURCES.map((s) => ({ id: s.id, kind: s.kind, url: "", title: s.label }));
  const held = info ? cards.map((c) => info[c.id]).filter(Boolean) : [];
  const totals = info ? held.reduce((a, s) => ({ chunks: a.chunks + s.chunks, files: a.files + s.files }), { chunks: 0, files: 0 }) : null;
  const loading = info === null || (scope && graph === null);

  return (
    <section className="page-pad">
      <header className="page-head">
        <div>
          <h1>Knowledge</h1>
          <p className="muted">Documents your agents can cite. Content is chunked, embedded locally with bge-m3 and searched only by agents that connect to it.</p>
        </div>
        <div className="head-actions">
          <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Collection">
            <option value="">Quick chat sources</option>
            {agents.map((a) => <option key={a.id} value={a.id}>Agent #{a.id} · {a.name}</option>)}
          </select>
          <button className="btn ghost" onClick={refresh} aria-label="Refresh"><Icon name="refresh" size={15} /></button>
        </div>
      </header>

      {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}

      <div className="kpi-row compact">
        <div className="kpi"><span className="kpi-label">Sources with content</span><strong>{info ? held.length : "—"}<small> / {cards.length}</small></strong></div>
        <div className="kpi"><span className="kpi-label">Files</span><strong>{totals ? totals.files : "—"}</strong></div>
        <div className="kpi"><span className="kpi-label">Chunks indexed</span><strong>{totals ? totals.chunks.toLocaleString() : "—"}</strong></div>
        <div className="kpi"><span className="kpi-label">Visible to</span><strong className="kpi-text">{scope ? `Agent #${scope} only` : "Quick chat only"}</strong></div>
      </div>

      {loading ? (
        <div className="card-grid">{[0, 1, 2].map((i) => <div key={i} className="source-card skeleton" />)}</div>
      ) : cards.length === 0 ? (
        <div className="empty">
          <Icon name="knowledge" size={26} />
          <h3>This agent has no source blocks</h3>
          <p className="muted">Add a PDF, Docs link or GitHub repo block in the Builder and connect it to the Agent.</p>
          <button className="btn primary" onClick={() => go("builder")}><Icon name="builder" size={15} /> Open Builder</button>
        </div>
      ) : (
        <div className="card-grid">
          {cards.map((c) => (
            <SourceCard key={`${scope}-${c.id}`} sourceId={c.id} kind={c.kind} graphId={scope ? Number(scope) : null}
              info={info[c.id]} initialUrl={c.url} title={c.title} onChanged={refresh} />
          ))}
        </div>
      )}
    </section>
  );
}
