// Shows one answer: the text as it streams, then the checked final answer with [n] markers as links to
// their citations, the tool calls the agent made, the model that answered (and any fallback), token
// counts (actual or estimated), the estimated cost, and the same tokens priced on the other models.
// Costs are estimates from prices.json, not a bill.
import { useState } from "react";
import { inr, secs, splitCitations, usd } from "./format.js";
import Icon from "./Icon.jsx";

function costText(cost) {
  if (!cost) return null;
  if (!cost.api_fee) return cost.local_compute_ms == null ? "No API cost" : `No API cost · ${secs(cost.local_compute_ms)} local compute`;
  if (!cost.priced) return "No published price";
  return `${inr(cost.inr)} est. (${usd(cost.usd)})`;
}

function comparisonText(rows, used) {
  const parts = rows
    .filter((r) => r.model !== used)
    .map((r) => {
      if (!r.api_fee) return `free locally (${r.label})`;
      if (!r.priced) return `${r.label}: no published price`;
      return `${inr(r.inr)} on ${r.label}`;
    });
  return parts.length ? `Same tokens: ${parts.join(" · ")}.` : null;
}

function AnswerText({ text, citations, onCite, active }) {
  const known = new Set((citations || []).map((c) => c.n));
  return (
    <p className="answer-text" lang="und">
      {splitCitations(text).map((p, i) =>
        p.text !== undefined ? <span key={i}>{p.text}</span> : known.has(p.cite) ? (
          <button key={i} type="button" className={`cite-chip${active === p.cite ? " on" : ""}`}
            onClick={() => onCite(p.cite)} aria-label={`Show citation ${p.cite}`}>{p.cite}</button>
        ) : <span key={i} className="cite-chip dead">{p.cite}</span>)}
    </p>
  );
}

export function ToolCalls({ calls }) {
  if (!calls?.length) return null;
  return (
    <ul className="tool-calls" aria-label="Tool calls">
      {calls.map((t, i) => (
        <li key={i} className={t.pending ? "pending" : t.ok ? "ok" : "err"}>
          <Icon name={t.tool === "calculator" ? "calculator" : "mcp"} size={14} />
          <span className="mono">{t.tool}({Object.values(t.args || {}).map((v) => JSON.stringify(v)).join(", ")})</span>
          {t.pending ? <span className="muted">running…</span> : t.ok ? (
            <span className="tool-out mono" title={t.output}>→ {t.tool === "calculator" ? t.output.split(" = ").pop() : "result returned"}</span>
          ) : <span className="tool-out err" title={t.error}>✕ {t.error}</span>}
          {t.ms !== undefined && <span className="muted small">{secs(t.ms)}</span>}
        </li>
      ))}
    </ul>
  );
}

export default function AnswerCard({ streamed, result, busy, toolCalls }) {
  const [active, setActive] = useState(null);
  if (!result && !streamed && !busy) return null;

  if (!result) {
    return (
      <div className="answer" aria-live="polite" aria-busy="true">
        <ToolCalls calls={toolCalls} />
        {streamed ? <p className="answer-text streaming">{streamed}<span className="caret" /></p> : (
          <div className="thinking"><span className="dots"><i /><i /><i /></span> Working on it…</div>
        )}
      </div>
    );
  }

  const used = result.model?.split(":")[0];
  const removed = result.claims?.filter((c) => c.verdict !== "supported") || [];
  let verdict = null;
  if (result.blocked) verdict = <span className="pill err"><Icon name="guardrails" size={12} /> Blocked by guardrails</span>;
  else if (result.supported) verdict = <span className="pill ok"><Icon name="check" size={12} /> Cited from your sources</span>;
  else if (result.grounded) verdict = <span className="pill warn"><Icon name="alert" size={12} /> No supporting source found</span>;
  else verdict = <span className="pill idle">Not from your sources</span>;

  return (
    <div className="answer" aria-live="polite">
      <ToolCalls calls={result.tool_calls} />
      <AnswerText text={result.answer || ""} citations={result.citations} onCite={(n) => setActive(active === n ? null : n)} active={active} />

      {result.citations?.length > 0 && (
        <ol className="citations">
          {result.citations.map((c) => (
            <li key={c.n} className={active === c.n ? "on" : ""}>
              <button type="button" className="citation-head" onClick={() => setActive(active === c.n ? null : c.n)}
                aria-expanded={active === c.n}>
                <span className="cite-chip static">{c.n}</span>
                <Icon name={c.file?.endsWith("(tool)") ? "tool" : c.origin ? "url" : "pdf"} size={14} />
                <span className="citation-file">{c.file}</span>
                <span className="muted small">{c.page ? `p. ${c.page} · ` : ""}chunk {c.chunk}</span>
              </button>
              {active === c.n && (
                <div className="citation-body">
                  <blockquote>{c.snippet}…</blockquote>
                  {c.origin && <a href={c.origin} target="_blank" rel="noreferrer" className="small"><Icon name="link" size={12} /> Open source</a>}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}

      <div className="answer-meta">
        {verdict}
        {result.model && <span className="pill"><Icon name={result.cost?.api_fee === false ? "cpu" : "cloud"} size={12} /> {result.model}</span>}
        {result.local_only && <span className="pill ok"><Icon name="lock" size={12} /> Local only</span>}
        {result.fallback && <span className="pill warn">Fallback from {result.fallback.from} ({result.fallback.code})</span>}
        {result.usage && (
          <span className="pill" title="input / output tokens">
            {result.usage.input_tokens.toLocaleString()} in · {result.usage.output_tokens.toLocaleString()} out{result.usage.estimated ? " (est.)" : ""}
          </span>
        )}
        {result.cost && <span className="pill">{costText(result.cost)}</span>}
        {result.response_ms !== null && result.response_ms !== undefined && <span className="pill"><Icon name="clock" size={12} /> {secs(result.response_ms)}</span>}
        {result.language && <span className="pill">{{ en: "English", te: "తెలుగు", hi: "हिन्दी" }[result.language] || result.language}</span>}
      </div>

      {result.model_note && <p className="note">{result.model_note}</p>}
      {removed.length > 0 && (
        <details className="note warn">
          <summary>{removed.length} sentence{removed.length > 1 ? "s" : ""} removed: the cited source did not back {removed.length > 1 ? "them" : "it"}</summary>
          <ul>{removed.map((c) => <li key={c.sentence}>“{c.sentence}” — {c.reason}</li>)}</ul>
        </details>
      )}
      {result.warnings?.map((w) => <p key={w} className="note warn"><Icon name="alert" size={13} /> {w}</p>)}
      {result.cost && result.comparison?.length > 0 && (
        <p className="note muted">{comparisonText(result.comparison, used)} Estimates, not a bill.</p>
      )}
    </div>
  );
}
