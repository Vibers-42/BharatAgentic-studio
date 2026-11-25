// Shown when the agent wants to run a tool that changes data (an approval_required event). The run is paused on the
// server until the person who asked clicks Approve or Deny (POST /pipeline/approvals/{id}); after the countdown the
// server denies it by itself. The final state comes from the approval_result event, not from the click.
import { useEffect, useState } from "react";
import { api } from "./api.js";
import Icon from "./Icon.jsx";

const DONE_TEXT = {
  approved: ["ok", "check", "Approved: the action ran."],
  denied: ["err", "x", "Denied: nothing was changed."],
  timeout: ["err", "clock", "No answer in time: denied, nothing was changed."],
};

export default function ApprovalCard({ ask, decision }) {
  const [left, setLeft] = useState(ask.timeout_s || 120);
  const [sending, setSending] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (decision) return undefined;
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [decision]);

  async function decide(approve) {
    setSending(approve ? "approve" : "deny");
    setError("");
    try {
      await api(`/pipeline/approvals/${ask.approval_id}`, { method: "POST", body: { approve } });
    } catch (err) {
      setError(err.message);
      setSending("");
    }
  }

  if (decision) {
    const [tone, icon, text] = DONE_TEXT[decision] || DONE_TEXT.denied;
    return (
      <div className={`approval done ${tone}`} role="status">
        <Icon name={icon} size={15} /> <span><b className="mono">{ask.tool}</b> · {text}</span>
      </div>
    );
  }
  return (
    <div className="approval" role="alertdialog" aria-label="Approval needed">
      <div className="approval-head">
        <Icon name="lock" size={16} />
        <strong>Approval needed</strong>
        <span className="pill warn push"><Icon name="clock" size={12} /> {left}s</span>
      </div>
      <p>The agent wants to run <b className="mono">{ask.tool}</b>. {ask.risk}</p>
      <pre className="approval-args mono">{JSON.stringify(ask.args, null, 2)}</pre>
      {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
      <div className="row-end">
        <button type="button" className="btn ghost subtle-danger" disabled={!!sending} onClick={() => decide(false)}>
          {sending === "deny" ? <span className="spinner" /> : <Icon name="x" size={14} />} Deny
        </button>
        <button type="button" className="btn primary" disabled={!!sending} onClick={() => decide(true)} autoFocus>
          {sending === "approve" ? <span className="spinner" /> : <Icon name="check" size={14} />} Approve
        </button>
      </div>
    </div>
  );
}
