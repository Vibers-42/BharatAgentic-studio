// Project chat. Ask the project's knowledge directly (every ready source of the project) or one of its shared
// agents. Conversations are saved by the api, are private to you, and a follow-up question sees the last few
// messages. The answer streams in from the real pipeline events; the stored message keeps the model that really
// answered, citations, tool calls, tokens and cost. A local-only project only offers local models (and the api
// refuses cloud models for it anyway).
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useConfirm } from "./App.jsx";
import ApprovalCard from "./ApprovalCard.jsx";
import AnswerCard from "./AnswerCard.jsx";
import { STAGES } from "./graph.js";
import Icon from "./Icon.jsx";
import { runPipeline } from "./sse.js";

const LANGUAGES = [["", "Auto language"], ["en", "English"], ["te", "తెలుగు · Telugu"], ["hi", "हिन्दी · Hindi"]];

function Stepper({ stages }) {
  return (
    <ol className="stepper" aria-label="Pipeline stages">
      {STAGES.map(([id, stage, label]) => <li key={id} className={`step ${stages[stage] || "idle"}`}><i />{label}</li>)}
    </ol>
  );
}

// The api's "I don't know" sentences (rag/prompt.py DONT_KNOW): a searched question with no supporting source.
const DONT_KNOW = ["I don't know based on the connected sources.", "కనెక్ట్ చేసిన మూలాల ఆధారంగా నాకు ఈ సమాచారం తెలియదు.",
  "जुड़े हुए स्रोतों के आधार पर मुझे यह जानकारी नहीं पता।", "Connect chesina sources lo ee samaachaaram naaku teliyadu.",
  "Jude hue sources ke aadhaar par mujhe yeh jaankari nahi pata."];

// A stored assistant message in the shape AnswerCard shows. A cited answer came from the sources; an "I don't know"
// came from searching them and finding nothing; anything else (a greeting, the app describing itself) did not use them.
function asResult(m) {
  const local = (m.model || "").startsWith("local");
  const cited = (m.citations || []).length > 0;
  return {
    answer: m.content, citations: m.citations || [], tool_calls: m.tool_calls || [], model: m.model, fallback: m.fallback,
    supported: cited, grounded: cited || DONT_KNOW.includes((m.content || "").trim()), blocked: false,
    usage: m.input_tokens != null ? { input_tokens: m.input_tokens, output_tokens: m.output_tokens } : null,
    cost: m.model ? { usd: m.cost_usd, inr: m.cost_inr, priced: m.cost_usd != null, api_fee: !local, local_compute_ms: null } : null,
  };
}

function uuid() {
  return (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
}

export default function Chat({ project }) {
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState("");     // "" = project knowledge chat
  const [models, setModels] = useState([]);
  const [model, setModel] = useState("local");
  const [language, setLanguage] = useState("");
  const [localOnly, setLocalOnly] = useState(project.local_only);
  const [convs, setConvs] = useState(null);
  const [active, setActive] = useState(null);     // conversation with messages
  const [live, setLive] = useState(null);         // the answer being streamed
  const [question, setQuestion] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(null);
  const [ask, dialog] = useConfirm();
  const endRef = useRef(null);
  const abortRef = useRef(null);

  const loadConvs = useCallback(() => api(`/conversations?project_id=${project.id}`).then(setConvs).catch(() => setConvs([])), [project.id]);
  useEffect(() => {
    loadConvs();
    api(`/graphs?project_id=${project.id}`).then(setAgents).catch(() => setAgents([]));
    api("/models").then((d) => {
      setModels(d.models);
      if (d.local_only) setLocalOnly(true);
      // Start on a model that is set up: the local model by default when it is installed, else the first ready one
      // (Groq, then Gemini), so a fresh install without the local models still answers.
      const ready = (k) => d.models.some((m) => m.key === k && m.ready);
      const order = d.local_only || project.local_only ? ["local", "local_small"] : ["local", "groq", "gemini", "local_small"];
      setModel((cur) => (ready(cur) ? cur : order.find(ready) || cur));
    }).catch(() => {});
    return () => abortRef.current?.abort();
  }, [project.id, loadConvs]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [active, live]);

  const forcedLocal = project.local_only;
  const offLocal = forcedLocal || localOnly;
  const visibleModels = models.filter((m) => !offLocal || m.local);
  useEffect(() => { if (offLocal && !models.find((m) => m.key === model)?.local) setModel("local"); }, [offLocal, models, model]);

  async function openConv(id) {
    setError("");
    try {
      const c = await api(`/conversations/${id}`);
      setActive(c);
      setAgentId(c.graph_id ? String(c.graph_id) : "");
    } catch (err) { setError(err.message); }
  }

  function newConv() { setActive(null); setLive(null); setError(""); }

  async function send(text) {
    const q = (text ?? question).trim();
    if (!q || live?.busy) return;
    setQuestion(""); setError("");
    let conv = active;
    try {
      if (!conv) {
        const created = await api("/conversations", { method: "POST", body: { project_id: project.id, graph_id: agentId ? Number(agentId) : null, title: q.slice(0, 80) } });
        conv = { ...created, messages: [] };
        setActive(conv);
        loadConvs();
      }
    } catch (err) { setError(err.message); return; }
    const turn = { question: q, stages: {}, streamed: "", result: null, tools: [], notices: [], approvals: [], busy: true, error: "" };
    setLive(turn);
    const patch = (fn) => setLive((t) => (t ? { ...t, ...fn(t) } : t));
    abortRef.current = new AbortController();
    let refused = false;
    try {
      await runPipeline({ question: q, conversation_id: conv.id, request_id: uuid(), model, language: language || null, local_only: offLocal }, (ev) => {
        if (ev.type === "stage") patch((t) => ({ stages: { ...t.stages, [ev.stage]: ev.status } }));
        if (ev.type === "token") patch((t) => ({ streamed: t.streamed + ev.text }));
        if (ev.type === "token_reset") patch((t) => ({ streamed: "", notices: ev.code === "about_fallback" ? t.notices
          : [...t.notices, `${ev.model} stopped mid-answer; its partial text was discarded.`] }));
        if (ev.type === "budget") patch((t) => ({ notices: [...t.notices, ev.message] }));
        if (ev.type === "fallback") patch((t) => ({ notices: [...t.notices, `${ev.from} failed (${ev.code}), so ${ev.to} answered instead.`] }));
        if (ev.type === "tool_call") patch((t) => ({ tools: [...t.tools, { node_id: ev.node_id, tool: ev.tool, args: ev.args, pending: true }] }));
        if (ev.type === "tool_result") patch((t) => ({ tools: [...t.tools.filter((x) => !(x.pending && x.node_id === ev.node_id)), ev] }));
        if (ev.type === "approval_required") patch((t) => ({ approvals: [...t.approvals, { ask: ev, decision: null }] }));
        if (ev.type === "approval_result") patch((t) => ({ approvals: t.approvals.map((x) => (x.ask.approval_id === ev.approval_id ? { ...x, decision: ev.decision } : x)) }));
        if (ev.type === "result") patch(() => ({ result: ev }));
        if (ev.type === "error") patch(() => ({ error: ev.code === "access_revoked" ? "Your access to this project was removed." : `${ev.code === "all_providers_failed" ? "No model could answer. " : ""}${ev.message}` }));
      }, abortRef.current.signal);
    } catch (err) {
      if (err.name === "AbortError") patch((t) => ({ notices: [...t.notices, "Stopped. The answer was not finished."] }));
      else { patch(() => ({ error: err.message })); refused = true; }
    }
    patch(() => ({ busy: false }));
    // A question refused before it started (spend limit, validation) is not stored, so keep it on screen with its
    // reason instead of reloading the conversation over it.
    if (refused) { loadConvs(); return; }
    try { setActive(await api(`/conversations/${conv.id}`)); setLive(null); loadConvs(); } catch { /* keep the live view */ }
  }

  const agent = agents.find((a) => String(a.id) === agentId);
  const copy = (text, id) => navigator.clipboard?.writeText(text).then(() => { setCopied(id); setTimeout(() => setCopied(null), 1500); });
  const messages = active?.messages || [];
  const pairs = [];
  for (let i = 0; i < messages.length; i += 1) {
    if (messages[i].role === "user") pairs.push({ q: messages[i], a: messages[i + 1]?.role === "assistant" ? messages[i + 1] : null });
  }

  return (
    <section className="chat-page with-history">
      <aside className="conv-list" aria-label="Conversations">
        <button className="btn primary" onClick={newConv}><Icon name="plus" size={15} /> New conversation</button>
        <div className="section-label">Your conversations in {project.name}</div>
        {convs === null ? <div className="skeleton-line" /> : convs.length === 0 ? <p className="muted small">None yet.</p> : (
          <ul className="list">
            {convs.map((c) => (
              <li key={c.id} className={`conv-item${active?.id === c.id ? " on" : ""}`}>
                <button className="list-btn" onClick={() => openConv(c.id)}><span><strong>{c.title}</strong>
                  <span className="muted small block">{c.graph_id ? agents.find((a) => a.id === c.graph_id)?.name || "agent" : "project knowledge"} · {new Date(c.updated_at).toLocaleDateString("en-IN")}</span></span></button>
                <span className="conv-actions">
                  <button className="btn ghost icon-btn sm" aria-label="Rename" title="Rename" onClick={async () => {
                    const t = window.prompt("New title", c.title);
                    if (t?.trim()) { await api(`/conversations/${c.id}`, { method: "PATCH", body: { title: t } }); loadConvs(); }
                  }}><Icon name="newfile" size={13} /></button>
                  <button className="btn ghost icon-btn sm" aria-label="Delete" title="Delete" onClick={() => ask(`Delete the conversation "${c.title}"?`, async () => {
                    await api(`/conversations/${c.id}`, { method: "DELETE" }); if (active?.id === c.id) newConv(); loadConvs();
                  })}><Icon name="trash" size={13} /></button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <div className="chat-main">
        <header className="chat-bar">
          <select value={agentId} onChange={(e) => { setAgentId(e.target.value); newConv(); }} aria-label="Ask" className="select-agent" disabled={!!active}>
            <option value="">Project knowledge (all ready sources)</option>
            {agents.map((a) => <option key={a.id} value={a.id}>Agent: {a.name}</option>)}
          </select>
          <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">
            {(visibleModels.length ? visibleModels : [{ key: "local", label: "Qwen 3 14B (local)", local: true, ready: true }]).map((m) => (
              <option key={m.key} value={m.key}>{m.label}{m.local ? "" : " · cloud"}{m.ready ? "" : " (not set up)"}</option>
            ))}
          </select>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Answer language">
            {LANGUAGES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <label className={`privacy-switch${offLocal ? " on" : ""}`} title={forcedLocal ? "This project is local-only" : "Only models on this computer"}>
            <input type="checkbox" role="switch" className="switch" checked={offLocal} disabled={forcedLocal} onChange={(e) => setLocalOnly(e.target.checked)} />
            <Icon name="lock" size={14} /> {forcedLocal ? "Local-only project" : "Local only"}
          </label>
        </header>

        <div className="messages" aria-live="polite">
          {!active && !live ? (
            <div className="empty chat-empty">
              <span className="hero-icon"><Icon name="sparkle" size={28} /></span>
              <h2>Ask {agent ? agent.name : `about ${project.name}`}</h2>
              <p className="muted">Answers come only from {agent ? "this agent's sources and tools" : "this project's ready knowledge"}, with citations you can check. Ask in English, తెలుగు or हिन्दी.</p>
              <div className="suggestions">
                {["What is this project about?", "Summarise the main rules in the documents.", "Which file should I read first?"].map((s) => (
                  <button key={s} className="suggestion" onClick={() => send(s)}>{s}</button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {pairs.map(({ q, a }) => (
                <div className="turn" key={q.id}>
                  <div className="msg user"><div className="bubble">{q.content}</div></div>
                  {a && (
                    <div className="msg bot">
                      <span className="avatar"><Icon name="agent" size={16} /></span>
                      <div className="bot-body">
                        {a.status === "error" ? <p className="inline-msg err"><Icon name="alert" size={14} /> {a.content}</p>
                          : <AnswerCard result={asResult(a)} />}
                        {a.status !== "error" && <button className="btn ghost sm copy-btn" onClick={() => copy(a.content, a.id)}>{copied === a.id ? "Copied" : "Copy answer"}</button>}
                      </div>
                    </div>
                  )}
                </div>
              ))}
              {live && (
                <div className="turn">
                  <div className="msg user"><div className="bubble">{live.question}</div></div>
                  <div className="msg bot">
                    <span className="avatar"><Icon name="agent" size={16} /></span>
                    <div className="bot-body">
                      <div className="bot-head"><strong>{agent ? agent.name : "Project knowledge"}</strong><Stepper stages={live.stages} /></div>
                      {live.notices.map((n) => <p key={n} className="note warn"><Icon name="alert" size={13} /> {n}</p>)}
                      {live.approvals.map((a) => <ApprovalCard key={a.ask.approval_id} ask={a.ask} decision={a.decision} />)}
                      {live.error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {live.error}</p>}
                      {(!live.error || live.result) && <AnswerCard streamed={live.streamed} result={live.result} busy={live.busy} toolCalls={live.tools} />}
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
          {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={1} aria-label="Question"
            placeholder={active ? "Ask a follow-up…" : "Ask in English, తెలుగు or हिन्दी…"}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          {live?.busy ? (
            <button type="button" className="btn icon-btn stop-btn" onClick={() => abortRef.current?.abort()} aria-label="Stop the answer" title="Stop">
              <span className="stop-square" />
            </button>
          ) : (
          <button className="btn primary icon-btn" disabled={!question.trim()} aria-label="Send">
            <Icon name="send" size={16} />
          </button>
          )}
        </form>
        <p className="composer-hint muted small">Enter to send · Shift+Enter for a new line · your conversations are private to you</p>
      </div>
      {dialog}
    </section>
  );
}
