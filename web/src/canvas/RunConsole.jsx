// The builder's run console: ask a question, then follow the run as the api reports it. Every line is a
// real Server-Sent Event from POST /pipeline/run (stage changes, tool calls and results, fallbacks,
// errors), stamped with the time it arrived. Token events are shown as the streaming answer instead.
import Icon from "../Icon.jsx";
import AnswerCard from "../AnswerCard.jsx";
import ApprovalCard from "../ApprovalCard.jsx";
import { secs } from "../format.js";

const STAGE_LABEL = { input: "Input", guardrails: "Guardrails", router: "Router", agent: "Agent", output: "Output" };

function describe(ev) {
  switch (ev.type) {
    case "stage": {
      const name = STAGE_LABEL[ev.stage] || ev.stage;
      if (ev.stage === "router" && ev.status === "done" && ev.detail?.route) return `${name} chose “${ev.detail.route}”: ${ev.detail.why}`;
      if (ev.stage === "agent" && ev.status === "done" && ev.detail?.retrieval) {
        const r = ev.detail.retrieval;
        return `${name} done · searched ${r.searched.join(", ")} · ${r.relevant} relevant chunk${r.relevant === 1 ? "" : "s"}`;
      }
      if (ev.status === "blocked") return `${name} blocked: ${ev.detail?.reason || "question refused"}`;
      return `${name} ${ev.status}${ev.ms !== undefined && ev.status !== "running" ? ` in ${secs(ev.ms)}` : ""}`;
    }
    case "tool_call": return `${ev.node_id} called ${ev.tool}(${JSON.stringify(ev.args)})`;
    case "tool_result": return ev.ok ? `${ev.node_id} returned ${ev.output.length > 90 ? `${ev.output.slice(0, 90)}…` : ev.output}` : `${ev.node_id} failed: ${ev.error}`;
    case "fallback": return `${ev.from} failed (${ev.code}); ${ev.to} answers instead`;
    case "approval_required": return `${ev.node_id} waits for your approval to run ${ev.tool}(${JSON.stringify(ev.args)})`;
    case "approval_result": return `Approval ${ev.decision}`;
    case "budget": return ev.message;
    case "token_reset": return `${ev.model} stopped mid-answer (${ev.code}); partial text discarded`;
    case "error": return `${ev.stage}: ${ev.message}`;
    case "result": return "Answer ready";
    default: return ev.type;
  }
}

function tone(ev) {
  if (ev.type === "error" || (ev.type === "tool_result" && !ev.ok) || ev.status === "blocked") return "err";
  if (ev.type === "fallback" || ev.type === "token_reset" || ev.status === "skipped" || ev.type === "approval_required" || ev.type === "budget") return "warn";
  if (ev.type === "approval_result") return ev.decision === "approved" ? "ok" : "err";
  if (ev.status === "running" || ev.type === "tool_call") return "running";
  return "ok";
}

export default function RunConsole({ question, setQuestion, onRun, busy, log, streamed, result, toolCalls, runError, agentInfo, inputRef, approvals = [], onStop }) {
  return (
    <div className="run-console">
      <form className="run-form" onSubmit={onRun}>
        <label htmlFor="run-q" className="field-label">Question</label>
        <textarea id="run-q" ref={inputRef} value={question} rows={3}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) onRun(e); }}
          placeholder="Ask in English, తెలుగు or हिन्दी, then watch the blocks light up…" />
        <div className="run-form-row">
          <span className="muted small">{agentInfo}</span>
          {busy ? (
            <button type="button" className="btn stop-btn" onClick={onStop}><span className="stop-square" /> Stop</button>
          ) : (
            <button className="btn primary" disabled={!question.trim()}><Icon name="play" size={14} /> Run</button>
          )}
        </div>
      </form>

      {runError && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {runError}</p>}

      {approvals.map((a) => <ApprovalCard key={a.ask.approval_id} ask={a.ask} decision={a.decision} />)}

      {(busy || result || streamed) && (
        <div className="run-answer">
          <div className="section-label">Answer</div>
          <AnswerCard streamed={streamed} result={result} busy={busy} toolCalls={toolCalls} />
        </div>
      )}

      {log.length > 0 && (
        <details className="event-log" open={busy && !streamed && !result}>
          <summary className="section-label">Live events <span className="muted">({log.length})</span></summary>
          <ol>
            {log.map((e, i) => (
              <li key={i} className={`ev ${tone(e.ev)}`}>
                <span className="ev-time mono">{secs(e.t)}</span>
                <span className="ev-dot" />
                <span className="ev-text">{describe(e.ev)}</span>
              </li>
            ))}
          </ol>
        </details>
      )}

      {!busy && !result && !log.length && (
        <div className="empty small-empty">
          <Icon name="sparkle" size={22} />
          <p>Run a question to see each block light up from the api's live events.</p>
          <div className="suggestions">
            {["What is the refund policy?",
              "My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?",
              "What is the status of order ORD-1002?",
              "Please cancel order ORD-1001."].map((q) => (
              <button key={q} type="button" className="suggestion" onClick={() => setQuestion(q)}>{q}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
