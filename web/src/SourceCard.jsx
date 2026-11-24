// One knowledge source: what it holds (chunks, files, when it was indexed) and how to fill it: drop or pick
// a PDF (with real upload progress), fetch a docs link, or download a GitHub repo. With a graphId the
// content belongs to that saved agent only (?graph_id=...); without one it is the quick chat's content.
// Status always comes from the api, so uploads made elsewhere (another tab, curl, a script) show too.
import { useState } from "react";
import { api, upload } from "./api.js";
import { timeAgo } from "./format.js";
import Icon from "./Icon.jsx";

const KIND = {
  pdf: { icon: "pdf", label: "PDF", verb: "Upload" },
  url: { icon: "url", label: "Docs link", verb: "Fetch", placeholder: "https://docs.example.com/page" },
  repo: { icon: "repo", label: "GitHub repo", verb: "Download", placeholder: "https://github.com/owner/repo" },
};

export default function SourceCard({ sourceId, kind, graphId, info, initialUrl = "", onChanged, onConfig, title }) {
  const spec = KIND[kind];
  const [link, setLink] = useState(initialUrl);
  const [phase, setPhase] = useState("");    // "" | "uploading" | "indexing"
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const query = graphId ? `?graph_id=${graphId}` : "";
  const busy = phase !== "";

  async function work(fn, successText) {
    setError("");
    setDone("");
    try {
      const r = await fn();
      setDone(successText(r));
      onChanged?.();
      return r;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setPhase("");
      setProgress(0);
    }
  }

  function sendFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError(`'${file.name}' is not a PDF file.`);
      return;
    }
    setPhase("uploading");
    work(async () => {
      // The node shows the stored file from the api's listing, so the graph itself does not change.
      return upload(`/sources/${sourceId}/pdf${query}`, file, (p) => {
        setProgress(p);
        if (p >= 1) setPhase("indexing");
      });
    }, (r) => `Indexed ${r.file}: ${r.chunks} chunks.`);
  }

  function fetchLink(e) {
    e.preventDefault();
    if (!link.trim()) return;
    setPhase("indexing");
    work(async () => {
      const r = await api(`/sources/${sourceId}/${kind}${query}`, { method: "POST", body: { url: link.trim() } });
      onConfig?.({ url: link.trim() });
      return r;
    }, (r) => `Indexed ${r.file || link}: ${r.chunks} chunks${r.files ? ` from ${r.files} files` : ""}.`);
  }

  async function clear() {
    setConfirm(false);
    setPhase("indexing");
    await work(() => api(`/sources/${sourceId}${query}`, { method: "DELETE" }),
      (r) => `Removed ${r.deleted_chunks} chunks.`);
  }

  return (
    <div className={`source-card${busy ? " is-busy" : ""}`} data-source={sourceId}>
      <div className="source-card-head">
        <span className={`chip-icon kind-${kind}`}><Icon name={spec.icon} size={16} /></span>
        <div className="source-card-title">
          <strong>{title || spec.label}</strong>
          <span className="muted mono small">{sourceId}</span>
        </div>
        <SourceStatus info={info} phase={phase} />
      </div>

      {info && !busy && (
        <div className="source-meta">
          <span title={info.file_names?.join("\n")}><Icon name={kind === "repo" ? "folder" : "pdf"} size={14} /> {info.files > 1 ? `${info.files} files` : info.file}</span>
          <span>{info.chunks} chunks</span>
          {info.ingested_at && <span title={new Date(info.ingested_at).toLocaleString()}>indexed {timeAgo(info.ingested_at)}</span>}
        </div>
      )}

      {kind === "pdf" ? (
        <label className={`dropzone${dragOver ? " over" : ""}${busy ? " disabled" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); if (!busy) sendFile(e.dataTransfer.files?.[0]); }}>
          <input type="file" accept="application/pdf,.pdf" disabled={busy} className="sr-only"
            onChange={(e) => { sendFile(e.target.files?.[0]); e.target.value = ""; }} />
          <Icon name="upload" size={18} />
          <span><b>{info ? "Replace PDF" : "Drop a PDF here"}</b> or click to choose</span>
        </label>
      ) : (
        <form className="link-form" onSubmit={fetchLink}>
          <input value={link} onChange={(e) => setLink(e.target.value)} placeholder={spec.placeholder}
            aria-label={`${spec.label} address`} disabled={busy} inputMode="url" />
          <button className="btn" disabled={busy || !link.trim()}>{spec.verb}</button>
        </form>
      )}

      {phase === "uploading" && (
        <div className="progress" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${Math.round(progress * 100)}%` }} />
          <em>Uploading {Math.round(progress * 100)}%</em>
        </div>
      )}
      {phase === "indexing" && (
        <div className="progress indeterminate" role="status">
          <span />
          <em>{kind === "repo" ? "Downloading, chunking and embedding…" : "Reading, chunking and embedding…"}</em>
        </div>
      )}
      {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
      {done && !error && <p className="inline-msg ok" role="status"><Icon name="check" size={14} /> {done}</p>}

      {info && !busy && (
        confirm ? (
          <div className="confirm-row" role="alertdialog" aria-label="Confirm removal">
            <span>Remove all {info.chunks} chunks of this source? This cannot be undone.</span>
            <button className="btn danger sm" onClick={clear}>Remove</button>
            <button className="btn ghost sm" onClick={() => setConfirm(false)}>Cancel</button>
          </div>
        ) : (
          <button className="btn ghost sm subtle-danger" onClick={() => setConfirm(true)}>
            <Icon name="trash" size={14} /> Clear content
          </button>
        )
      )}
    </div>
  );
}

export function SourceStatus({ info, phase }) {
  if (phase) return <span className="pill running"><span className="spinner" /> Indexing</span>;
  if (info) return <span className="pill ok"><Icon name="check" size={12} /> Indexed</span>;
  return <span className="pill idle">Empty</span>;
}
