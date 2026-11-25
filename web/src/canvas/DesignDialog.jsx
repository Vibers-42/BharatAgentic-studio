// "Describe your use case, get a ready agent". The api asks a free model for a small design and compiles it
// into a valid graph (POST /pipeline/design); this dialog only collects the description and shows progress.
// DesignNotes is shown beside the canvas afterwards: why each block was chosen, what is still to add, and
// example questions to try.
import { useState } from "react";
import { api } from "../api.js";
import { Modal } from "../App.jsx";
import Icon from "../Icon.jsx";

const EXAMPLES = [
  "Answer patient questions about our clinic's refund and cancellation policy in Telugu, and work out refund amounts.",
  "Help students of our college find admission dates, fees and hostel rules from the prospectus, in Hindi.",
  "Support agent for our shop: look up order status and answer questions from our returns policy page.",
];

export default function DesignDialog({ project, onClose, onDesigned, initial = "" }) {
  const [description, setDescription] = useState(initial);
  const [localOnly, setLocalOnly] = useState(!!project.local_only);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await api("/pipeline/design", { method: "POST", body: { description, project_id: project.id, local_only: localOnly } });
      onDesigned(data);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <Modal title="Generate an agent from a description" onClose={() => !busy && onClose()}>
      <form className="stack form" onSubmit={submit}>
        <label className="field">
          <span className="field-label">What should the agent do, and for whom?</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={4} maxLength={1500} autoFocus
            disabled={busy} placeholder="e.g. Answer patient questions from our clinic's refund policy in Telugu" />
          <span className="field-hint">The agent picks its sources, tools, language and guardrails from this. It replaces the current canvas; nothing is saved until you save it.</span>
        </label>
        <div className="stack">
          <span className="field-label">Or start from an example</span>
          <div className="design-examples">
            {EXAMPLES.map((x) => (
              <button key={x} type="button" className="suggestion" disabled={busy} onClick={() => setDescription(x)}>{x}</button>
            ))}
          </div>
        </div>
        <label className="switch-row">
          <span>
            <span className="switch-label">Local only</span>
            <span className="field-hint">Design it with the model on this computer, and keep the agent local.</span>
          </span>
          <input type="checkbox" role="switch" className="switch" checked={localOnly} disabled={busy || project.local_only}
            onChange={(e) => setLocalOnly(e.target.checked)} aria-label="Local only" />
        </label>
        {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
        <div className="row-end">
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary" disabled={busy || description.trim().length < 10}>
            {busy ? <span className="spinner" /> : <Icon name="sparkle" size={15} />} {busy ? "Designing your agent…" : "Generate agent"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function DesignNotes({ design, onSelectNode, onAsk, onDismiss }) {
  const g = design.generation;
  return (
    <div className="inspector design-notes">
      <div className="inspector-head">
        <span className="chip-icon group-main"><Icon name="sparkle" size={16} /></span>
        <div>
          <strong>{design.name}</strong>
          <div className="muted small">Designed by {g.label} in {(g.ms / 1000).toFixed(1)} s{g.fallback ? ` (after ${g.fallback.from} failed)` : ""}</div>
        </div>
        <button className="btn ghost icon-btn sm push" onClick={onDismiss} aria-label="Close the design notes"><Icon name="x" size={14} /></button>
      </div>
      {design.summary && <p>{design.summary}</p>}

      {design.reasons?.length > 0 && (
        <div className="stack">
          <span className="field-label">Why these blocks</span>
          <ul className="design-list">{design.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}

      <div className="stack">
        <span className="field-label">Next steps</span>
        <ol className="design-steps">
          {design.reused_sources?.map((t) => (
            <li key={t} className="done"><Icon name="check" size={13} /> Uses the project's “{t}”</li>
          ))}
          {design.todo.map((s) => (
            <li key={s.node_id}>
              <button type="button" className="linkish" onClick={() => onSelectNode(s.node_id)}>{s.text}</button>
              <span className="muted small"> · block {s.node_id}</span>
            </li>
          ))}
          <li>Ask a question in Run; Save to share the agent with the project.</li>
        </ol>
      </div>

      {design.suggested_questions?.length > 0 && (
        <div className="stack">
          <span className="field-label">Try asking</span>
          <div className="design-examples">
            {design.suggested_questions.map((q) => (
              <button key={q} type="button" className="suggestion" onClick={() => onAsk(q)}>{q}</button>
            ))}
          </div>
        </div>
      )}
      <p className="muted small">Click any block to change it. The design is checked by the same rules as a hand-drawn agent.</p>
    </div>
  );
}
