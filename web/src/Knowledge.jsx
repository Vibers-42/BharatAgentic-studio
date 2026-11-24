// Project knowledge: every source of the active project with what the api really knows about it (status,
// who added it, when, chunks, size, revision, link, error), and the actions the user's role allows: add a file
// (PDF, TXT, Markdown, DOCX, CSV, XLSX), a docs link or a GitHub repo; re-index, replace, download, delete.
// Indexing runs inside the request: the status shows "Uploading x%" while the bytes are sent and "Indexing"
// until the api answers (no invented progress).
import { useCallback, useEffect, useState } from "react";
import { api, upload } from "./api.js";
import { useConfirm } from "./App.jsx";
import { timeAgo } from "./format.js";
import Icon from "./Icon.jsx";

const ACCEPT = ".pdf,.txt,.md,.markdown,.docx,.csv,.xlsx";
const KIND_ICON = { pdf: "pdf", url: "url", repo: "repo", text: "pdf", markdown: "pdf", docx: "pdf", csv: "dashboard", xlsx: "dashboard" };
const size = (b) => (b == null ? "" : b < 1024 * 1024 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`);

export default function Knowledge({ project, reloadProjects }) {
  const [sources, setSources] = useState(null);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState(0);
  const [msg, setMsg] = useState(null);
  const [link, setLink] = useState("");
  const [linkKind, setLinkKind] = useState("url");
  const [open, setOpen] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [ask, dialog] = useConfirm();
  const can = project.can_contribute;

  const load = useCallback(() => api(`/projects/${project.id}/sources`).then(setSources).catch((err) => setMsg({ tone: "err", text: err.message })), [project.id]);
  useEffect(() => { load(); }, [load]);

  async function run(label, fn, okText) {
    setBusy(label); setMsg(null); setProgress(0);
    try { const r = await fn(); setMsg({ tone: "ok", text: okText(r) }); }
    catch (err) { setMsg({ tone: "err", text: err.message }); }
    finally { setBusy(""); load(); reloadProjects(); }
  }
  const sendFile = (file) => file && run(`Uploading ${file.name}`, () => upload(`/projects/${project.id}/sources/file`, file, (p) => { setProgress(p); if (p >= 1) setBusy(`Indexing ${file.name}`); }),
    (r) => `${r.title} is ready: ${r.chunk_count} chunks.`);
  const addLink = (e) => {
    e.preventDefault();
    const url = link.trim();
    run(`Indexing ${url}`, () => api(`/projects/${project.id}/sources/${linkKind}`, { method: "POST", body: { url } }), (r) => { setLink(""); return `${r.title} is ready: ${r.chunk_count} chunks.`; });
  };

  const shown = (sources || []).filter((s) => !filter || `${s.title} ${s.kind} ${s.uploaded_by_name} ${s.url || ""}`.toLowerCase().includes(filter.toLowerCase()));
  const ready = (sources || []).filter((s) => s.status === "ready");

  return (
    <section className="page-pad">
      <header className="page-head">
        <div><h1>Knowledge</h1><p className="muted">{project.name}: added once, used by everyone on the project and by its agents.</p></div>
        <div className="head-actions"><input type="search" placeholder="Search sources" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Search sources" /></div>
      </header>
      <div className="kpi-row compact">
        <div className="kpi"><span className="kpi-label">Ready sources</span><strong>{sources ? ready.length : "—"}<small> / {sources?.length ?? "—"}</small></strong></div>
        <div className="kpi"><span className="kpi-label">Chunks indexed</span><strong>{sources ? ready.reduce((n, s) => n + s.chunk_count, 0).toLocaleString() : "—"}</strong></div>
        <div className="kpi"><span className="kpi-label">Your role</span><strong className="kpi-text">{project.can_manage ? "manager" : project.my_project_role}</strong></div>
        <div className="kpi"><span className="kpi-label">Privacy</span><strong className="kpi-text">{project.local_only ? "Local only" : "Cloud allowed"}</strong></div>
      </div>

      {can ? (
        <div className="card-grid two">
          <label className={`dropzone big${dragOver ? " over" : ""}${busy ? " disabled" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); if (!busy) sendFile(e.dataTransfer.files?.[0]); }}>
            <input type="file" accept={ACCEPT} disabled={!!busy} className="sr-only" aria-label="Upload a file"
              onChange={(e) => { sendFile(e.target.files?.[0]); e.target.value = ""; }} />
            <Icon name="upload" size={22} />
            <span><b>Drop a file here</b> or click to choose<br /><span className="muted small">PDF, Word (.docx), Markdown, TXT, CSV, Excel (.xlsx)</span></span>
          </label>
          <form className="card stack" onSubmit={addLink}>
            <div className="segmented" role="radiogroup" aria-label="Link type">
              <button type="button" role="radio" aria-checked={linkKind === "url"} className={linkKind === "url" ? "on" : ""} onClick={() => setLinkKind("url")}>Docs page</button>
              <button type="button" role="radio" aria-checked={linkKind === "repo"} className={linkKind === "repo" ? "on" : ""} onClick={() => setLinkKind("repo")}>GitHub repo</button>
            </div>
            <input value={link} onChange={(e) => setLink(e.target.value)} disabled={!!busy} inputMode="url" aria-label={linkKind === "repo" ? "GitHub repository address" : "Docs page address"}
              placeholder={linkKind === "repo" ? "https://github.com/owner/repo (public)" : "https://docs.example.com/page"} />
            <button className="btn" disabled={!!busy || !link.trim()}>{linkKind === "repo" ? "Download and index" : "Fetch and index"}</button>
            <span className="field-hint">{linkKind === "repo" ? "A snapshot of the default branch; re-index to pick up changes." : "One page; links on it are not followed. Private and internal addresses are refused."}</span>
          </form>
        </div>
      ) : <p className="note"><Icon name="info" size={14} /> You can read and ask about this knowledge. Contributors and managers add sources.</p>}

      {busy && (
        <div className={`progress${progress > 0 && progress < 1 ? "" : " indeterminate"}`} role="status">
          <span style={progress > 0 && progress < 1 ? { width: `${Math.round(progress * 100)}%` } : undefined} />
          <em>{busy}{progress > 0 && progress < 1 ? ` ${Math.round(progress * 100)}%` : " (reading, chunking and embedding)…"}</em>
        </div>
      )}
      {msg && <p className={`inline-msg ${msg.tone}`} role={msg.tone === "err" ? "alert" : "status"}><Icon name={msg.tone === "err" ? "alert" : "check"} size={14} /> {msg.text}</p>}

      <div className="card">
        {sources === null ? <div className="skeleton-line" /> : shown.length === 0 ? (
          <div className="empty small-empty"><Icon name="knowledge" size={22} /><p>{sources.length ? "No source matches the search." : "No knowledge in this project yet."}</p></div>
        ) : (
          <ul className="source-list">
            {shown.map((s) => (
              <li key={s.id} className={`source-row${open === s.id ? " open" : ""}`}>
                <button className="source-row-head" onClick={() => setOpen(open === s.id ? null : s.id)} aria-expanded={open === s.id}>
                  <span className={`chip-icon kind-${s.kind}`}><Icon name={KIND_ICON[s.kind] || "pdf"} size={15} /></span>
                  <span className="grow"><strong>{s.title}</strong>
                    <span className="muted small block">{s.kind.toUpperCase()} · added by {s.uploaded_by_name} · {timeAgo(s.created_at)}{s.size_bytes ? ` · ${size(s.size_bytes)}` : ""}{s.chunk_count ? ` · ${s.chunk_count} chunks` : ""}{s.used_by_agents ? ` · used by ${s.used_by_agents} agent${s.used_by_agents > 1 ? "s" : ""}` : ""}</span></span>
                  <span className={`pill ${s.status === "ready" ? "ok" : s.status === "failed" ? "err" : "running"}`}>{s.status}</span>
                </button>
                {open === s.id && (
                  <div className="source-detail">
                    <dl className="facts">
                      {s.url && <><dt>Link</dt><dd><a href={s.url} target="_blank" rel="noreferrer">{s.url}</a></dd></>}
                      {s.revision && <><dt>Revision</dt><dd className="mono">{s.revision}</dd></>}
                      {s.original_name && <><dt>File</dt><dd>{s.original_name}</dd></>}
                      {s.content_hash && <><dt>SHA-256</dt><dd className="mono small">{s.content_hash.slice(0, 16)}…</dd></>}
                      <dt>Updated</dt><dd>{new Date(s.updated_at).toLocaleString("en-IN")}</dd>
                      {s.error && <><dt>Problem</dt><dd className="err-text">{s.error}</dd></>}
                    </dl>
                    <div className="row-wrap">
                      {s.storage_path && <a className="btn sm" href={`/api/sources/${s.id}/file`}><Icon name="upload" size={13} /> Download</a>}
                      {can && (s.kind === "url" || s.kind === "repo" || s.storage_path) && (
                        <button className="btn sm" disabled={!!busy} onClick={() => run(`Re-indexing ${s.title}`, () => api(`/sources/${s.id}/reindex`, { method: "POST" }), (r) => `${r.title}: ${r.chunk_count} chunks (${r.status}).`)}><Icon name="refresh" size={13} /> Re-index</button>
                      )}
                      {can && !["url", "repo"].includes(s.kind) && (
                        <label className="btn sm"><Icon name="upload" size={13} /> Replace file
                          <input type="file" accept={ACCEPT} className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) run(`Replacing with ${f.name}`, () => upload(`/sources/${s.id}/file`, f, setProgress), (r) => `${r.title}: ${r.chunk_count} chunks.`); }} /></label>
                      )}
                      {can && (
                        <button className="btn ghost sm subtle-danger" onClick={() => ask(`Delete "${s.title}"? Its chunks and stored file are removed${s.used_by_agents ? ` and ${s.used_by_agents} agent(s) lose it` : ""}. This cannot be undone.`,
                          () => run(`Deleting ${s.title}`, () => api(`/sources/${s.id}`, { method: "DELETE" }), () => `Deleted ${s.title}.`))}><Icon name="trash" size={13} /> Delete</button>
                      )}
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {dialog}
    </section>
  );
}
