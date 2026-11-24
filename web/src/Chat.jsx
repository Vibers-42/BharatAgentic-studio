// Chat: talk to an agent. "Quick chat" uses the quick-chat sources (managed on the Knowledge page) and the
// tools you switch on here; or pick an agent saved in the Builder and it runs exactly as drawn, over its
// own documents. Each answer streams in from the real pipeline events, with the five stages, tool calls,
// citations, the model, tokens and estimated cost.
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import AnswerCard from "./AnswerCard.jsx";
import { buildGraph, SOURCES, STAGES, TOOLS } from "./graph.js";
import Icon from "./Icon.jsx";
import { runPipeline } from "./sse.js";

const LANGUAGES = [
  ["", "Auto language"],
  ["en", "English"],
  ["te", "తెలుగు · Telugu"],
  ["hi", "हिन्दी · Hindi"],
];

const SUGGESTIONS = [
  "What is the refund policy?",
  "My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?",
  "What is the status of order ORD-1002?",
  "రీఫండ్ పాలసీ ఏమిటి?",
  "रिफंड नीति क्या है?",
];

const STATUS_WORD = { running: "running", done: "done", blocked: "blocked", skipped: "skipped", error: "failed" };

function Stepper({ stages }) {
  return (
    <ol className="stepper" aria-label="Pipeline stages">
      {STAGES.map(([id, stage, label]) => {
        const s = stages[stage];
        return (
          <li key={id} className={`step ${s || "idle"}`} data-node={id} title={s ? `${label}: ${STATUS_WORD[s] || s}` : label}>
            <i />{label}
          </li>
        );
      })}
    </ol>
  );
}

function Turn({ turn }) {
  return (
    <div className="turn">
      <div className="msg user"><div className="bubble">{turn.question}</div></div>
      <div className="msg bot">
        <span className="avatar"><Icon name="agent" size={16} /></span>
        <div className="bot-body">
          <div className="bot-head">
            <strong>{turn.agentName}</strong>
            <Stepper stages={turn.stages} />
          </div>
          {turn.notices.map((n) => <p key={n} className="note warn"><Icon name="alert" size={13} /> {n}</p>)}
          {turn.error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {turn.error}</p>}
          {!turn.error || turn.result ? (
            <AnswerCard streamed={turn.streamed} result={turn.result} busy={turn.busy} toolCalls={turn.tools} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Toggle({ on, onChange, icon, label, detail, disabled }) {
  return (
    <label className={`toggle-card${on ? " on" : ""}${disabled ? " disabled" : ""}`}>
      <span className="chip-icon"><Icon name={icon} size={15} /></span>
      <span className="toggle-text"><strong>{label}</strong><span className="muted small">{detail}</span></span>
      <input type="checkbox" role="switch" className="switch" checked={on} disabled={disabled} onChange={(e) => onChange(e.target.checked)} aria-label={`Use ${label}`} />
    </label>
  );
}

export default function Chat({ user, go }) {
  const [models, setModels] = useState([]);
  const [model, setModel] = useState("local");
  const [language, setLanguage] = useState("");
  const [localOnly, setLocalOnly] = useState(false);
  const [serverLocalOnly, setServerLocalOnly] = useState(false);
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState("");          // "" = quick chat
  const [agentGraph, setAgentGraph] = useState(null);
  const [sourceInfo, setSourceInfo] = useState({});
  const [useSource, setUseSource] = useState({ chat_pdf: true, chat_repo: true, chat_url: true });
  const [useTool, setUseTool] = useState({ chat_calc: true, chat_orders: false });
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const endRef = useRef(null);
  const busy = turns.some((t) => t.busy);

  useEffect(() => {
    api("/models").then((d) => {
      setModels(d.models);
      const firstReady = d.models.find((m) => m.ready && m.key === "local") || d.models.find((m) => m.ready);
      if (firstReady) setModel(firstReady.key);
      if (d.local_only) {
        setServerLocalOnly(true);
        setLocalOnly(true);
        setModel("local");
      }
    }).catch(() => {});
    api("/graphs").then(setAgents).catch(() => {});
  }, []);

  // What the chosen agent can use, and what each of its sources holds (from the api).
  useEffect(() => {
    setAgentGraph(null);
    const scope = agentId ? `?graph_id=${agentId}` : "";
    api(`/sources${scope}`).then((d) => setSourceInfo(Object.fromEntries(d.sources.map((s) => [s.source_id, s])))).catch(() => setSourceInfo({}));
    if (agentId) {
      api(`/graphs/${agentId}`).then((g) => {
        setAgentGraph(g);
        const agentNode = g.nodes.find((n) => n.type === "agent");
        if (agentNode?.config.model && !(localOnly && !models.find((m) => m.key === agentNode.config.model)?.local)) setModel(agentNode.config.model);
      }).catch(() => {});
    }
  }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);

  const patch = (id, fn) => setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...fn(t) } : t)));
  const agentName = agentId ? agents.find((a) => String(a.id) === String(agentId))?.name || `Agent #${agentId}` : "Quick chat";

  async function ask(text) {
    const q = (text ?? question).trim();
    if (!q || busy) return;
    setQuestion("");
    const id = Date.now();
    setTurns((ts) => [...ts, { id, question: q, agentName, stages: {}, streamed: "", result: null, error: "", notices: [], tools: [], busy: true }]);

    let body;
    if (agentId) {
      body = { question: q, graph_id: Number(agentId), model, language: language || null, user, local_only: localOnly };
    } else {
      const sources = SOURCES.filter((s) => sourceInfo[s.id]).map((s) => ({
        id: s.id, type: s.type, connected: useSource[s.id],
        config: s.kind === "pdf" ? { file: sourceInfo[s.id].file } : s.kind === "url" ? { url: "" } : {},
      }));
      const tools = TOOLS.map((t) => ({ ...t, connected: useTool[t.id] }));
      body = { question: q, language: language || null, graph: buildGraph({ sources, tools, language, model }), model, user, local_only: localOnly };
    }
    try {
      await runPipeline(body, (ev) => {
        if (ev.type === "stage") patch(id, (t) => ({ stages: { ...t.stages, [ev.stage]: ev.status } }));
        if (ev.type === "token") patch(id, (t) => ({ streamed: t.streamed + ev.text }));
        if (ev.type === "token_reset") patch(id, (t) => ({ streamed: "", notices: [...t.notices, `${ev.model} stopped mid-answer (${ev.code}); its partial text was discarded.`] }));
        if (ev.type === "fallback") patch(id, (t) => ({ notices: [...t.notices, `${ev.from} failed (${ev.code}), so ${ev.to} is answering instead.`] }));
        if (ev.type === "tool_call") patch(id, (t) => ({ tools: [...t.tools, { node_id: ev.node_id, tool: ev.tool, args: ev.args, pending: true }] }));
        if (ev.type === "tool_result") patch(id, (t) => ({ tools: [...t.tools.filter((x) => !(x.pending && x.node_id === ev.node_id)), ev] }));
        if (ev.type === "result") patch(id, () => ({ result: ev }));
        if (ev.type === "error") {
          const quota = ev.code?.startsWith("quota_") ? "Model quota reached. " : "";
          const all = ev.code === "all_providers_failed" ? "No model could answer. " : "";
          patch(id, (t) => ({ error: `${all}${quota}${ev.message}`, stages: { ...t.stages, [ev.stage]: "error" } }));
        }
      });
    } catch (err) {
      patch(id, () => ({ error: err.message }));
    } finally {
      patch(id, () => ({ busy: false }));
    }
  }

  const visibleModels = (models.length ? models : [{ key: "local", label: "Qwen 3 14B", local: true, ready: true }]).filter((m) => !localOnly || m.local);
  const agentSources = agentGraph ? agentGraph.nodes.filter((n) => n.type.startsWith("source_")) : [];
  const agentTools = agentGraph ? agentGraph.nodes.filter((n) => n.type.startsWith("tool_")) : [];
  const linked = (id) => agentGraph?.edges.some((e) => e.from === id && e.to === agentGraph.nodes.find((n) => n.type === "agent")?.id);

  return (
    <section className="chat-page">
      <div className="chat-main">
        <header className="chat-bar">
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)} aria-label="Agent" className="select-agent">
            <option value="">Quick chat</option>
            {agents.map((a) => <option key={a.id} value={a.id}>#{a.id} · {a.name}</option>)}
          </select>
          <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">
            {visibleModels.map((m) => (
              <option key={m.key} value={m.key}>{m.label}{m.local ? "" : " · cloud"}{m.ready ? "" : " (not set up)"}</option>
            ))}
          </select>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Answer language">
            {LANGUAGES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <label className={`privacy-switch${localOnly ? " on" : ""}`} title="Only models running on this computer; nothing is sent to a cloud AI, not even as fallback">
            <input type="checkbox" role="switch" className="switch" checked={localOnly} disabled={serverLocalOnly}
              onChange={(e) => {
                setLocalOnly(e.target.checked);
                if (e.target.checked && !models.find((m) => m.key === model)?.local) setModel("local");
              }} />
            <Icon name="lock" size={14} /> Local only
          </label>
          <button className="btn ghost sm show-md" onClick={() => setPanelOpen((o) => !o)} aria-expanded={panelOpen}>
            <Icon name="knowledge" size={14} /> Sources & tools
          </button>
        </header>

        <div className="messages" aria-live="polite">
          {turns.length === 0 ? (
            <div className="empty chat-empty">
              <span className="hero-icon"><Icon name="sparkle" size={28} /></span>
              <h2>Ask {agentName === "Quick chat" ? "your documents" : agentName}</h2>
              <p className="muted">Answers come only from connected sources and tools, with citations you can check. Ask in English, తెలుగు or हिन्दी.</p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => <button key={s} className="suggestion" onClick={() => ask(s)} disabled={busy}>{s}</button>)}
              </div>
            </div>
          ) : turns.map((t) => <Turn key={t.id} turn={t} />)}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={1} aria-label="Question"
            placeholder="Ask in English, తెలుగు or हिन्दी…"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }} />
          <button className="btn primary icon-btn" disabled={busy || !question.trim()} aria-label="Send">
            {busy ? <span className="spinner" /> : <Icon name="send" size={16} />}
          </button>
        </form>
        <p className="composer-hint muted small">Enter to send · Shift+Enter for a new line · costs are estimates, not a bill</p>
      </div>

      <aside className={`chat-side${panelOpen ? " open" : ""}`} aria-label="Sources and tools">
        {agentId ? (
          <>
            <div className="section-label">This agent's sources</div>
            {agentGraph === null && <div className="skeleton-line" />}
            {agentGraph && agentSources.length === 0 && <p className="muted small">No sources on this agent.</p>}
            {agentSources.map((n) => (
              <div key={n.id} className={`toggle-card readonly${linked(n.id) ? " on" : ""}`}>
                <span className="chip-icon"><Icon name={n.type === "source_repo" ? "repo" : "url" in n.config ? "url" : "pdf"} size={15} /></span>
                <span className="toggle-text">
                  <strong>{n.id}</strong>
                  <span className="muted small">{sourceInfo[n.id] ? `${sourceInfo[n.id].chunks} chunks · ${sourceInfo[n.id].file}` : "empty"}{linked(n.id) ? "" : " · not connected"}</span>
                </span>
              </div>
            ))}
            <div className="section-label">Tools</div>
            {agentGraph && agentTools.length === 0 && <p className="muted small">No tools on this agent.</p>}
            {agentTools.map((n) => (
              <div key={n.id} className={`toggle-card readonly${linked(n.id) ? " on" : ""}`}>
                <span className="chip-icon"><Icon name={n.type === "tool_mcp" ? "mcp" : "calculator"} size={15} /></span>
                <span className="toggle-text"><strong>{n.type === "tool_mcp" ? `MCP tool · ${n.config.server}` : "Calculator"}</strong><span className="muted small">{n.id}{linked(n.id) ? "" : " · not connected"}</span></span>
              </div>
            ))}
            <button className="btn ghost sm" onClick={() => go("builder")}><Icon name="builder" size={14} /> Edit in Builder</button>
          </>
        ) : (
          <>
            <div className="section-label">Sources</div>
            {SOURCES.map((s) => (
              <Toggle key={s.id} icon={s.kind} label={s.label} on={!!sourceInfo[s.id] && useSource[s.id]} disabled={!sourceInfo[s.id]}
                detail={sourceInfo[s.id] ? `${sourceInfo[s.id].chunks} chunks · ${sourceInfo[s.id].file}` : "empty: add it in Knowledge"}
                onChange={(on) => setUseSource((u) => ({ ...u, [s.id]: on }))} />
            ))}
            <div className="section-label">Tools</div>
            {TOOLS.map((t) => (
              <Toggle key={t.id} icon={t.kind} label={t.label} on={useTool[t.id]}
                detail={t.kind === "calculator" ? "exact arithmetic for amounts" : "read-only order lookups"}
                onChange={(on) => setUseTool((u) => ({ ...u, [t.id]: on }))} />
            ))}
            <button className="btn ghost sm" onClick={() => go("knowledge")}><Icon name="knowledge" size={14} /> Manage knowledge</button>
          </>
        )}
      </aside>
    </section>
  );
}
