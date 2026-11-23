// Chat screen: add sources (PDF, GitHub repo, docs link), choose which are connected to the agent, pick
// a model, ask a question, and watch the five stages and the answer stream in from the real pipeline events.
import { useEffect, useState } from "react";
import { buildGraph, SOURCES, STAGES } from "./graph.js";
import { runPipeline } from "./sse.js";
import AnswerCard from "./AnswerCard.jsx";
import SourcePanel from "./SourcePanel.jsx";

const LANGUAGES = [
  ["", "Auto (from question)"],
  ["en", "English"],
  ["te", "తెలుగు (Telugu)"],
  ["hi", "हिन्दी (Hindi)"],
];

export default function Chat({ user }) {
  const [sources, setSources] = useState({}); // source id -> { info, config, connected }
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
    fetch("/api/models")
      .then((r) => r.json())
      .then((data) => setModels(data.models))
      .catch(() => {});
  }, []);

  async function ask(e) {
    e.preventDefault();
    setBusy(true);
    setStages({});
    setStreamed("");
    setResult(null);
    setError("");
    setNotices([]);
    // Only sources that hold content and are ticked "connected" become nodes with a line to the agent.
    const connectedSources = SOURCES.filter((src) => sources[src.id]?.info?.chunks > 0)
      .map((src) => ({
        id: src.id,
        type: src.type,
        config: sources[src.id].config || (src.kind === "pdf" ? { file: sources[src.id].info.file } : {}),
        connected: sources[src.id].connected ?? true,
      }));
    const graph = buildGraph({ sources: connectedSources, language, model });
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
      <SourcePanel state={sources} setState={setSources} />

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
