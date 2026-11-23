// Shows the answer: the text as it streams, then the checked final answer with its citations, the model
// that answered (and any fallback), token counts (actual or estimated), the estimated cost, and the same
// tokens priced on the other models. Costs are estimates from prices.json, not a bill.
const inr = (v) => (v === null || v === undefined ? null : `₹${v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)}`);

function costText(cost) {
  if (!cost) return null;
  if (!cost.api_fee) return `no API cost · ${(cost.local_compute_ms / 1000).toFixed(1)} s of local compute`;
  if (!cost.priced) return "no published price, cost not estimated";
  return `${inr(cost.inr)} est. ($${cost.usd.toFixed(6)})`;
}

function comparisonText(rows, used) {
  const parts = rows
    .filter((r) => r.model !== used)
    .map((r) => {
      if (!r.api_fee) return `no API cost locally (${r.label})`;
      if (!r.priced) return `${r.label}: no published price`;
      return `${inr(r.inr)} on ${r.label}`;
    });
  return parts.length ? `Same tokens: ${parts.join(", ")}.` : null;
}

export default function AnswerCard({ streamed, result, busy }) {
  if (!result && !streamed && !busy) return null;
  if (!result) {
    return (
      <div className="card answer">
        <p className="streaming">{streamed || "Thinking…"}</p>
      </div>
    );
  }

  const used = result.model?.split(":")[0];
  return (
    <div className="card answer">
      <p className={result.blocked ? "error" : ""}>{result.answer}</p>

      {result.citations?.length > 0 && (
        <ol className="citations">
          {result.citations.map((c) => (
            <li key={c.n} value={c.n}>
              {c.origin ? (
                <a href={c.origin} target="_blank" rel="noreferrer"><strong>{c.file}</strong></a>
              ) : (
                <strong>{c.file}</strong>
              )}
              {c.page ? `, page ${c.page}` : ""}, chunk {c.chunk}
              <span className="muted"> · similarity {c.similarity}</span>
              <blockquote>{c.snippet}…</blockquote>
            </li>
          ))}
        </ol>
      )}

      <div className="badges">
        {result.model && <span className="badge">{result.model}</span>}
        {result.fallback && (
          <span className="badge warn">fallback from {result.fallback.from} ({result.fallback.code})</span>
        )}
        {result.usage && (
          <span className="badge">
            {result.usage.input_tokens} in / {result.usage.output_tokens} out tokens
            {result.usage.estimated ? " (estimated)" : ""}
          </span>
        )}
        {result.cost && <span className="badge">{costText(result.cost)}</span>}
        {result.response_ms !== null && result.response_ms !== undefined && (
          <span className="badge">{(result.response_ms / 1000).toFixed(1)} s</span>
        )}
        {!result.blocked && result.grounded && !result.supported && (
          <span className="badge warn">No supporting source found</span>
        )}
        {!result.blocked && !result.grounded && <span className="badge warn">Not from your sources</span>}
        {result.supported && <span className="badge ok">Cited from your sources</span>}
        <span className="badge">{result.language}</span>
      </div>
      {result.model_note && <p className="muted small">{result.model_note}</p>}
      {result.claims?.some((c) => c.verdict !== "supported") && (
        <p className="notice small">
          Removed because the cited source did not back them:{" "}
          {result.claims.filter((c) => c.verdict !== "supported").map((c) => `"${c.sentence}" (${c.reason})`).join("; ")}
        </p>
      )}
      {result.cost && result.comparison?.length > 0 && (
        <p className="muted small">{comparisonText(result.comparison, used)} Estimates, not a bill.</p>
      )}
      {result.warnings?.map((w) => (
        <p key={w} className="notice">{w}</p>
      ))}
    </div>
  );
}
