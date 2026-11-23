// Chat screen: upload a PDF source, choose whether it is connected to the agent, pick a model, ask a
// question, and watch the five stages and the answer stream in from the real pipeline events.
import { useEffect, useState } from "react";
import { buildGraph, SOURCE_ID, STAGES } from "./graph.js";
import { runPipeline } from "./sse.js";
import AnswerCard from "./AnswerCard.jsx";

const LANGUAGES = [
  ["", "Auto (from question)"],
  ["en", "English"],
  ["te", "తెలుగు (Telugu)"],
  ["hi", "हिन्दी (Hindi)"],
];

export default function Chat({ user }) {
  const [source, setSource] = useState(null); // {file, chunks} once a PDF is ingested
  const [connected, setConnected] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [language, setLanguage] = useState("");
  const [question, setQuestion] = useState("");
  const [stages, setStages] = useState({}); // node id -> running/done/blocked/skipped
  const [streamed, setStreamed] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState([]);
  const [model, setModel] = useState("gemini");
  const [notices, setNotices] = useState([]); // fallbacks and discarded partial answers, as they happen

  useEffect(() => {
    fetch(`/api/sources/${SOURCE_ID}`)
      .then((r) => r.json())
      .then((info) => info.chunks > 0 && setSource(info))
      .catch(() => {});
    fetch("/api/models")
      .then((r) => r.json())
      .then((data) => setModels(data.models))
      .catch(() => {});
  }, []);

  async function upload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch(`/api/sources/${SOURCE_ID}/pdf`, { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Upload failed (HTTP ${res.status})`);
      setSource(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function ask(e) {
    e.preventDefault();
    setBusy(true);
    setStages({});
    setStreamed("");
    setResult(null);
    setError("");
    setNotices([]);
    const graph = buildGraph({ fileName: source?.file, connected, language, model });
    try {
      await runPipeline({ question, language: language || null, graph, model, user }, (ev) => {
        if (ev.type === "stage") setStages((s) => ({ ...s, [ev.node_id]: ev.status }));
        if (ev.type === "token") setStreamed((t) => t + ev.text);
        if (ev.type === "token_reset") {
          setStreamed("");
          setNotices((n) => [...n, `${ev.model} stopped mid-answer (${ev.code}); its partial text was discarded.`]);
        }
        if (ev.type === "fallback") {
          setNotices((n) => [...n, `${ev.from} failed (${ev.code}), so the ${ev.to} model is answering instead.`]);
        }
        if (ev.type === "result") setResult(ev);
        if (ev.type === "error") {
          setStages((s) => ({ ...s, [ev.node_id]: "error" }));
          const quota = ev.code?.startsWith("quota_") ? "Model quota reached. " : "";
          const all = ev.code === "all_providers_failed" ? "No model could answer. " : "";
          setError(`${all}${quota}${ev.stage}: ${ev.message}`);
        }
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="card">
        <h2>Source</h2>
        <label className="upload">
          <input type="file" accept="application/pdf,.pdf" onChange={upload} disabled={uploading} />
          {uploading ? "Reading and embedding…" : source ? "Replace PDF" : "Upload a PDF"}
        </label>
        {source && (
          <p className="muted">
            {source.file}: {source.chunks} chunks stored
            <label className="check">
              <input type="checkbox" checked={connected} onChange={(e) => setConnected(e.target.checked)} />
              Connected to agent
            </label>
          </p>
        )}
      </div>

      <ol className="pipeline" aria-label="Pipeline stages">
        {STAGES.map(([id, , label]) => (
          <li key={id} className={`stage ${stages[id] || "idle"}`} data-node={id}>
            {label}
          </li>
        ))}
      </ol>

      <form className="ask" onSubmit={ask}>
        <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">
          {(models.length ? models : [{ key: "gemini", label: "Gemini", ready: true }]).map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}{m.local ? " · local" : ""}{m.ready ? "" : " (not set up)"}
            </option>
          ))}
        </select>
        <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Answer language">
          {LANGUAGES.map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask in English, తెలుగు or हिन्दी…"
          aria-label="Question"
        />
        <button disabled={busy}>{busy ? "Answering…" : "Ask"}</button>
      </form>

      {notices.map((n) => (
        <p key={n} className="notice">{n}</p>
      ))}
      {error && <p className="error">{error}</p>}
      <AnswerCard streamed={streamed} result={result} busy={busy} />
    </section>
  );
}
