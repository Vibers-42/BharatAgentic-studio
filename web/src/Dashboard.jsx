// Usage dashboard (handoff: total requests, tokens and cost; a breakdown by model and by user; a daily
// chart). Reads the real usage API with the chosen filters (user, model, period); costs are estimates
// from prices.json, not a bill. Local models have no API fee, so their cost is ₹0 by design.
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { compact, fillDays, inr, istToday, niceMax, secs, usd } from "./format.js";
import Icon from "./Icon.jsx";

const PERIODS = [[7, "7 days"], [14, "14 days"], [30, "30 days"], [90, "90 days"]];

function barPath(x, y, w, h, r = 4) {
  // A bar with rounded data-end (top) and a square baseline.
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

// The chart is drawn at the container's real pixel width, so text stays readable on phones and wide screens.
function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(240, Math.round(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

function DailyChart({ days }) {
  const [hover, setHover] = useState(null);
  const [table, setTable] = useState(false);
  const [boxRef, W] = useWidth();
  const H = 220;
  const pad = { l: 40, r: 8, t: 12, b: 26 };
  const nice = niceMax(Math.max(...days.map((d) => d.requests)));
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const step = iw / days.length;
  const bw = Math.max(3, Math.min(28, step - 2));
  const ticks = [0, Math.round(nice / 2), nice];
  const labelEvery = Math.ceil(days.length / Math.max(2, Math.floor(iw / 56)));
  const h = hover !== null ? days[hover] : null;

  return (
    <div className="chart-card card">
      <div className="card-head">
        <h3>Requests per day</h3>
        <span className="muted small">India time</span>
        <button className="btn ghost sm push" onClick={() => setTable((t) => !t)} aria-pressed={table}>{table ? "Chart" : "Table"}</button>
      </div>
      {table ? (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Day</th><th>Requests</th><th>Tokens</th><th>Est. cost</th><th>Failed</th></tr></thead>
            <tbody>{days.filter((d) => d.requests).map((d) => (
              <tr key={d.day}><td>{d.day}</td><td>{d.requests}</td><td>{compact(d.input_tokens + d.output_tokens)}</td><td>{inr(d.cost_inr)}</td><td>{d.errors}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <div className="chart-box" ref={boxRef} onMouseLeave={() => setHover(null)}>
          <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="chart" role="img" aria-label={`Requests per day over the last ${days.length} days`}>
            {ticks.map((t) => {
              const y = pad.t + ih - (ih * t) / nice;
              return (
                <g key={t}>
                  <line x1={pad.l} x2={W - pad.r} y1={y} y2={y} className="grid-line" />
                  <text x={pad.l - 6} y={y + 4} textAnchor="end" className="axis-text">{t}</text>
                </g>
              );
            })}
            {days.map((d, i) => {
              const bh = (ih * d.requests) / nice;
              const x = pad.l + i * step + (step - bw) / 2;
              return (
                <g key={d.day}>
                  {d.requests > 0 && <path d={barPath(x, pad.t + ih - bh, bw, bh)} className={`bar${hover === i ? " on" : ""}`} />}
                  {i % labelEvery === 0 && <text x={x + bw / 2} y={H - 8} textAnchor="middle" className="axis-text">{d.day.slice(8)}/{d.day.slice(5, 7)}</text>}
                  <rect x={pad.l + i * step} y={pad.t} width={step} height={ih} fill="transparent"
                    onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={d.requests ? 0 : -1}
                    aria-label={`${d.day}: ${d.requests} requests`} />
                </g>
              );
            })}
          </svg>
          {h && (
            <div className="tooltip" style={{ left: Math.min(W - 90, Math.max(90, pad.l + hover * step + step / 2)) }}>
              <strong>{h.day}</strong>
              <span>{h.requests} request{h.requests === 1 ? "" : "s"}{h.errors ? ` · ${h.errors} failed` : ""}</span>
              <span>{compact(h.input_tokens + h.output_tokens)} tokens · {inr(h.cost_inr)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Bars({ title, rows, label, value, format, sub, empty }) {
  const max = Math.max(1e-9, ...rows.map(value));
  const sorted = [...rows].sort((a, b) => value(b) - value(a));
  return (
    <div className="card chart-card">
      <div className="card-head"><h3>{title}</h3></div>
      {sorted.length === 0 ? <p className="muted small">{empty || "No usage in this period."}</p> : (
        <ul className="hbars">
          {sorted.map((r) => (
            <li key={label(r)} title={`${label(r)}: ${format(value(r))}`}>
              <span className="hbar-label">{label(r)}{sub && <small className="muted"> {sub(r)}</small>}</span>
              <span className="hbar-track"><span className="hbar-fill" style={{ width: `${Math.max(value(r) > 0 ? 2 : 0, (100 * value(r)) / max)}%` }} /></span>
              <span className="hbar-value">{format(value(r))}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Kpi({ label, value, sub, icon }) {
  return (
    <div className="kpi">
      <span className="kpi-label"><Icon name={icon} size={14} /> {label}</span>
      <strong>{value}</strong>
      {sub && <small className="muted">{sub}</small>}
    </div>
  );
}

const modelName = (m) => m.replace(/^([a-z_]+):/, (_, k) => `${{ gemini: "Gemini", groq: "Groq", local: "Local", local_small: "Local backup" }[k] || k} · `);

export default function Dashboard({ org, isAdmin }) {
  const [models, setModels] = useState([]);
  const [scope, setScope] = useState("me");   // "me" or "workspace" (owners and admins); the api enforces it
  const [model, setModel] = useState("");
  const [days, setDays] = useState(14);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/models").then((d) => setModels(d.models)).catch(() => {});
  }, []);

  useEffect(() => {
    const q = (extra) => {
      const p = new URLSearchParams({ days: String(days), ...extra });
      if (scope === "workspace" && org) p.set("org_id", String(org.id));
      if (model) p.set("model", model);
      return `?${p}`;
    };
    setData(null);
    setError("");
    Promise.all([
      api(`/usage/totals${q({})}`),
      api(`/usage/by-model${q({})}`.replace(/&?model=[^&]*/, "")),
      api(`/usage/by-user${q({})}`),
      api(`/usage/by-day${q({})}`),
      api(`/usage/recent${q({ limit: "12" })}`),
      api(`/usage/by-project${q({})}`),
    ])
      .then(([totals, byModel, byUser, byDay, recent, byProject]) => setData({ totals, byModel, byUser, byDay, recent, byProject }))
      .catch((err) => setError(`Cannot load usage: ${err.message}`));
  }, [scope, model, days, org?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = data?.totals;
  const shownByModel = data ? data.byModel.filter((r) => !model || r.model.startsWith(`${model}:`)) : [];

  return (
    <section className="page-pad dashboard">
      <header className="page-head">
        <div>
          <h1>Usage</h1>
          <p className="muted">{scope === "workspace" ? `Everyone in ${org?.name}: requests, tokens and estimated cost per project, model and person (never message content).` : "Your own requests, tokens and estimated cost."} Estimates from the price table, not a bill.</p>
        </div>
      </header>

      <div className="filters" role="group" aria-label="Filters">
        <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Whose usage">
          <option value="me">My usage</option>
          {isAdmin && org?.kind === "company" && <option value="workspace">Whole workspace: {org.name}</option>}
        </select>
        <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model filter">
          <option value="">All models</option>
          {models.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
        </select>
        <div className="segmented" role="radiogroup" aria-label="Period">
          {PERIODS.map(([d, label]) => (
            <button key={d} role="radio" aria-checked={days === d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{label}</button>
          ))}
        </div>
      </div>

      {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}

      <div className="kpi-row">
        {t ? (
          <>
            <Kpi icon="chat" label="Requests" value={compact(t.requests)} sub={`${t.errors} failed · ${t.fallbacks} fallbacks`} />
            <Kpi icon="dashboard" label="Tokens" value={compact(t.input_tokens + t.output_tokens)} sub={`${compact(t.input_tokens)} in · ${compact(t.output_tokens)} out`} />
            <Kpi icon="sparkle" label="Estimated cost" value={inr(t.cost_inr)} sub={`${usd(t.cost_usd)}${t.unpriced_requests ? ` · ${t.unpriced_requests} unpriced` : ""}`} />
            <Kpi icon="clock" label="Avg answer time" value={secs(t.avg_response_ms)} sub={`${secs(t.local_compute_ms)} local compute`} />
          </>
        ) : [0, 1, 2, 3].map((i) => <div key={i} className="kpi skeleton" />)}
      </div>

      {data && t.requests === 0 ? (
        <div className="empty">
          <Icon name="dashboard" size={26} />
          <h3>No usage for these filters</h3>
          <p className="muted">Ask a question in Chat or run an agent in the Builder, then come back.</p>
        </div>
      ) : data ? (
        <>
          <DailyChart days={fillDays(data.byDay, days, istToday())} />
          <div className="grid2">
            <Bars title="Estimated cost by model" rows={shownByModel} label={(r) => modelName(r.model)} value={(r) => r.cost_inr} format={inr} />
            <Bars title="Requests by model" rows={shownByModel} label={(r) => modelName(r.model)} value={(r) => r.requests} format={compact}
              sub={(r) => (r.errors ? `${r.errors} failed` : "")} />
            <Bars title="Requests by project" rows={data.byProject} label={(r) => r.project} value={(r) => r.requests} format={compact} />
            <Bars title={scope === "workspace" ? "Tokens by person" : "Your tokens"} rows={data.byUser} label={(r) => r.user} value={(r) => r.input_tokens + r.output_tokens} format={compact} />
          </div>
          <div className="card">
            <div className="card-head"><h3>Latest answers</h3></div>
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>When</th><th>User</th><th>Model</th><th>Tokens in / out</th><th>Cost</th><th>Time</th><th>Status</th></tr></thead>
                <tbody>
                  {data.recent.map((r) => (
                    <tr key={r.id}>
                      <td>{new Date(r.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</td>
                      <td>{r.user}</td>
                      <td className="mono small">{r.model}{r.fallback_from ? ` (from ${r.fallback_from})` : ""}</td>
                      <td>{r.input_tokens.toLocaleString()} / {r.output_tokens.toLocaleString()}{r.tokens_estimated ? " est." : ""}</td>
                      <td>{r.cost_inr === null ? "no price" : inr(r.cost_inr)}</td>
                      <td>{secs(r.response_ms)}</td>
                      <td><span className={`pill ${r.status === "ok" ? "ok" : "err"}`}>{r.status === "ok" ? "ok" : r.error_code || r.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : (
        <div className="card skeleton tall" />
      )}
    </section>
  );
}
