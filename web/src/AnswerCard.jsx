// Shows the answer: the text as it streams, then the checked final answer with its citations,
// the model badge and token counts. Cost per answer is added in Phase 4.
export default function AnswerCard({ streamed, result, busy }) {
  if (!result && !streamed && !busy) return null;
  if (!result) {
    return (
      <div className="card answer">
        <p className="streaming">{streamed || "Thinking…"}</p>
      </div>
    );
  }

  return (
    <div className="card answer">
      <p className={result.blocked ? "error" : ""}>{result.answer}</p>

      {result.citations?.length > 0 && (
        <ol className="citations">
          {result.citations.map((c) => (
            <li key={c.n} value={c.n}>
              <strong>{c.file}</strong>
              {c.page ? `, page ${c.page}` : ""}, chunk {c.chunk}
              <span className="muted"> · similarity {c.similarity}</span>
              <blockquote>{c.snippet}…</blockquote>
            </li>
          ))}
        </ol>
      )}

      <div className="badges">
        {result.model && <span className="badge">{result.model}</span>}
        {result.usage?.input_tokens > 0 && (
          <span className="badge">
            {result.usage.input_tokens} in / {result.usage.output_tokens} out tokens
          </span>
        )}
        {!result.blocked && result.grounded && !result.supported && (
          <span className="badge warn">No supporting source found</span>
        )}
        {!result.blocked && !result.grounded && <span className="badge warn">Not from your sources</span>}
        {result.supported && <span className="badge ok">Cited from your sources</span>}
        <span className="badge">{result.language}</span>
      </div>
    </div>
  );
}
