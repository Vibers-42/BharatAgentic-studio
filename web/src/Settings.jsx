// Settings: the company's name and description (owners), and your password. Changing the password signs out
// every other session of your account.
import { useState } from "react";
import { api } from "./api.js";
import Icon from "./Icon.jsx";

export default function Settings({ org, session, reloadMe }) {
  const [form, setForm] = useState({ name: org?.name || "", description: "" });
  const [pw, setPw] = useState({ current_password: "", new_password: "", confirm_password: "" });
  const [msg, setMsg] = useState(null);
  const say = (tone, text) => setMsg({ tone, text });
  async function saveOrg(e) {
    e.preventDefault();
    try { await api(`/orgs/${org.id}`, { method: "PATCH", body: form }); await reloadMe(); say("ok", "Saved."); } catch (err) { say("err", err.message); }
  }
  async function savePw(e) {
    e.preventDefault();
    try { await api("/auth/password", { method: "POST", body: pw }); await reloadMe(); setPw({ current_password: "", new_password: "", confirm_password: "" }); say("ok", "Password changed. Other sessions were signed out."); }
    catch (err) { say("err", err.message); }
  }
  return (
    <section className="page-pad narrow">
      <header className="page-head"><div><h1>Settings</h1></div></header>
      {msg && <p className={`inline-msg ${msg.tone}`} role="status"><Icon name={msg.tone === "ok" ? "check" : "alert"} size={14} /> {msg.text}</p>}
      {org?.kind === "company" && org.role === "owner" && (
        <form className="card stack form" onSubmit={saveOrg}>
          <h3>Company</h3>
          <label className="field"><span className="field-label">Name</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} /></label>
          <label className="field"><span className="field-label">Description</span><textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <button className="btn primary">Save company</button>
        </form>
      )}
      <form className="card stack form" onSubmit={savePw}>
        <h3>Your account</h3>
        <p className="muted small">{session.user.full_name} · {session.user.email}</p>
        <label className="field"><span className="field-label">Current password</span><input type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} required /></label>
        <label className="field"><span className="field-label">New password</span><input type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} required minLength={10} /></label>
        <label className="field"><span className="field-label">Confirm new password</span><input type="password" autoComplete="new-password" value={pw.confirm_password} onChange={(e) => setPw({ ...pw, confirm_password: e.target.value })} required /></label>
        <button className="btn">Change password</button>
      </form>
    </section>
  );
}
