// Number and text formatting shared by the chat, the builder and the dashboard (unit-tested).

export function inr(v) {
  if (v === null || v === undefined) return "—";
  if (v === 0) return "₹0";
  if (Math.abs(v) < 1) return `₹${v.toFixed(4)}`; // per-answer cloud costs are fractions of a rupee
  return `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function usd(v) {
  if (v === null || v === undefined) return "—";
  return `$${v < 0.01 ? v.toFixed(6) : v.toFixed(4)}`;
}

export function secs(ms) {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function compact(n) {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  return n.toLocaleString("en-IN");
}

export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

// Split an answer into text and [n] citation markers, so markers can be rendered as links.
export function splitCitations(text) {
  const parts = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push({ text: text.slice(last, m.index) });
    parts.push({ cite: Number(m[1]) });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

// Fill the days the api did not return (no requests) so a daily chart has a continuous axis.
// Days are "YYYY-MM-DD" in India time; `today` is that day for the last bar.
export function fillDays(rows, days, today) {
  const byDay = Object.fromEntries(rows.map((r) => [r.day, r]));
  const end = new Date(`${today}T00:00:00Z`);
  const out = [];
  for (let i = days - 1; i >= 0; i -= 1) {
    const d = new Date(end.getTime() - i * 86400000).toISOString().slice(0, 10);
    out.push(byDay[d] || { day: d, requests: 0, cost_inr: 0, input_tokens: 0, output_tokens: 0, errors: 0 });
  }
  return out;
}

// Today in India time (UTC+5:30, no daylight saving), as YYYY-MM-DD.
export function istToday(now = Date.now()) {
  return new Date(now + 330 * 60000).toISOString().slice(0, 10);
}

// A round axis maximum at or above v: 1, 2, 5, 10, 20, 50, 100, ...
export function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 5, 10].map((m) => m * p).find((x) => x >= v);
}

// Tool arguments as shown on an answer: one argument by value ("1200 * 80%"), several with their names, because
// stored arguments come back in a different key order (order_id: "ORD-1004", reason: "...").
export function toolArgs(args) {
  const entries = Object.entries(args || {});
  if (entries.length === 1) return JSON.stringify(entries[0][1]);
  return entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ");
}
