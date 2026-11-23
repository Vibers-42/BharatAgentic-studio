// Usage dashboard (handoff: total requests, tokens and cost; a breakdown by model and by user; a daily
// chart). Reads the real usage API; costs are estimates from prices.json, not a bill.
import { useEffect, useState } from "react";

const inr = (v) => `₹${(v || 0).toFixed((v || 0) < 1 ? 4 : 2)}`;
const secs = (ms) => `${((ms || 0) / 1000).toFixed(1)} s`;

function Bars({ rows, label, value, format }) {
  const max = Math.max(1, ...rows.map(value));
  return (
    <div className="bars">
      {rows.map((r) => (
        <div key={label(r)} className="bar-row">
          <span className="bar-label" title={label(r)}>{label(r)}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(100 * value(r)) / max}%` }} /></span>
          <span className="bar-value">{format(value(r))}</span>
        </div>
      ))}
      {rows.length === 0 && <p className="muted">No usage yet.</p>}
    </div>
  );
}

function DailyChart({ days }) {
  const width = 640;
  const height = 160;
  const max = Math.max(1, ...days.map((d) => d.requests));
  const step = days.length ? width / days.length : width;
  return (
    <svg className="daily" viewBox={`0 0 ${width} ${height + 30}`} role="img" aria-label="Requests per day">
      {days.map((d, i) => {
        const h = (height * d.requests) / max;
        return (
          <g key={d.day}>
            <rect x={i * step + 4} y={height - h} width={Math.max(4, step - 8)} height={h} rx="3">
              <title>{`${d.day}: ${d.requests} requests, ${inr(d.cost_inr)}`}</title>
            </rect>
            <text x={i * step + step / 2} y={height + 14} textAnchor="middle">{d.day.slice(5)}</text>
            <text x={i * step + step / 2} y={Math.max(12, height - h - 4)} textAnchor="middle">{d.requests}</text>
          </g>
        );
      })}
    </svg>
  );
}

export default function Dashboard() {
  const [users, setUsers] = useState([]);
  const [user, setUser] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/users").then((r) => r.json()).then(setUsers).catch(() => {});
  }, []);

  useEffect(() => {
    const q = user ? `?user=${encodeURIComponent(user)}` : "";
    const and = user ? `&user=${encodeURIComponent(user)}` : "";
    Promise.all([
      fetch(`/api/usage/totals${q}`).then((r) => r.json()),
      fetch(`/api/usage/by-model${q}`).then((r) => r.json()),
      fetch("/api/usage/by-user").then((r) => r.json()),
      fetch(`/api/usage/by-day?days=14${and}`).then((r) => r.json()),
      fetch(`/api/usage/recent?limit=15${and}`).then((r) => r.json()),
    ])
      .then(([totals, byModel, byUser, byDay, recent]) => setData({ totals, byModel, byUser, byDay, recent }))
      .catch(() => setError("Cannot load usage. Is the api running?"));
  }, [user]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading usage…</p>;
  const t = data.totals;

  return (
    <section className="dashboard">
      <div className="toolbar card">
        <strong>Usage</strong>
        <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="Usage for user">
          <option value="">All users</option>
          {users.map((u) => <option key={u.name} value={u.name}>{u.name}</option>)}
        </select>
        <span className="muted">Costs are estimates from the price table, not a bill. Local models have no API fee.</span>
      </div>

      <div className="stats">
        <div className="stat card"><span>Requests</span><strong>{t.requests}</strong><small>{t.errors} failed · {t.fallbacks} fallbacks</small></div>
        <div className="stat card"><span>Tokens</span><strong>{(t.input_tokens + t.output_tokens).toLocaleString()}</strong><small>{t.input_tokens.toLocaleString()} in · {t.output_tokens.toLocaleString()} out</small></div>
        <div className="stat card"><span>Estimated cost</span><strong>{inr(t.cost_inr)}</strong><small>${t.cost_usd.toFixed(6)} · {t.unpriced_requests} unpriced</small></div>
        <div className="stat card"><span>Local compute</span><strong>{secs(t.local_compute_ms)}</strong><small>avg answer {secs(t.avg_response_ms)}</small></div>
      </div>

      <div className="grid2">
        <div className="card"><h3>Requests by model</h3>
          <Bars rows={data.byModel} label={(r) => r.model} value={(r) => r.requests} format={(v) => v} /></div>
        <div className="card"><h3>Cost by model</h3>
          <Bars rows={data.byModel} label={(r) => r.model} value={(r) => r.cost_inr} format={inr} /></div>
        <div className="card"><h3>Requests by user</h3>
          <Bars rows={data.byUser} label={(r) => r.user} value={(r) => r.requests} format={(v) => v} /></div>
        <div className="card"><h3>Tokens by user</h3>
          <Bars rows={data.byUser} label={(r) => r.user} value={(r) => r.input_tokens + r.output_tokens} format={(v) => v.toLocaleString()} /></div>
      </div>

      <div className="card"><h3>Requests per day (last 14 days, India time)</h3><DailyChart days={data.byDay} /></div>

      <div className="card">
        <h3>Latest answers</h3>
        <table className="usage-table">
          <thead><tr><th>When</th><th>User</th><th>Model</th><th>Tokens in/out</th><th>Cost</th><th>Time</th><th>Status</th></tr></thead>
          <tbody>
            {data.recent.map((r) => (
              <tr key={r.id}>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.user}</td>
                <td>{r.model}{r.fallback_from ? ` (from ${r.fallback_from})` : ""}</td>
                <td>{r.input_tokens}/{r.output_tokens}{r.tokens_estimated ? " est." : ""}</td>
                <td>{r.cost_inr === null ? "no price" : inr(r.cost_inr)}</td>
                <td>{secs(r.response_ms)}</td>
                <td>{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
