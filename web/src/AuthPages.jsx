// Sign in, register, invitation acceptance and "session ended" pages. Passwords are only ever sent to the api
// over the same origin; nothing about the session is stored in the browser except the HttpOnly cookie.
import { useEffect, useState } from "react";
import { api } from "./api.js";
import Icon from "./Icon.jsx";

function AuthFrame({ title, subtitle, children, themeButton }) {
  return (
    <div className="auth-page">
      <header className="landing-bar">
        <a className="brand-inline" href="#/"><span className="brand-dot" /> BharatAgent Studio</a>
        {themeButton}
      </header>
      <main className="auth-card card" id="main">
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
        {children}
      </main>
    </div>
  );
}

function Field({ label, ...props }) {
  return <label className="field"><span className="field-label">{label}</span><input {...props} /></label>;
}

export function Login({ onSignedIn, themeButton, after }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const me = await api("/auth/login", { method: "POST", body: { email, password } });
      onSignedIn(me);
      if (!after) window.location.hash = "#/app/overview";
    } catch (err) { setError(err.message); setBusy(false); }
  }
  return (
    <AuthFrame title="Sign in" subtitle="Welcome back." themeButton={themeButton}>
      <form className="stack form" onSubmit={submit}>
        <Field label="Email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        <Field label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
        <button className="btn primary" disabled={busy}>{busy && <span className="spinner" />} Sign in</button>
        {!after && <p className="muted small">New here? <a href="#/register">Create an account</a></p>}
        <p className="muted small">Forgot your password? Password reset by email is not available yet; ask a workspace owner.</p>
      </form>
    </AuthFrame>
  );
}

export function Register({ onSignedIn, themeButton, after, presetEmail = "" }) {
  const [form, setForm] = useState({ full_name: "", email: presetEmail, password: "", confirm_password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const mismatch = form.confirm_password && form.password !== form.confirm_password;
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const me = await api("/auth/register", { method: "POST", body: form });
      onSignedIn(me);
      if (!after) window.location.hash = "#/app/overview";
    } catch (err) { setError(err.message); setBusy(false); }
  }
  return (
    <AuthFrame title="Create your account" subtitle="You get a private personal workspace. Create or join a company any time." themeButton={themeButton}>
      <form className="stack form" onSubmit={submit}>
        <Field label="Full name" autoComplete="name" value={form.full_name} onChange={set("full_name")} required maxLength={100} autoFocus />
        <Field label="Email" type="email" autoComplete="email" value={form.email} onChange={set("email")} required />
        <Field label="Password" type="password" autoComplete="new-password" value={form.password} onChange={set("password")} required minLength={10} />
        <span className="field-hint">At least 10 characters, with a letter and a digit.</span>
        <Field label="Confirm password" type="password" autoComplete="new-password" value={form.confirm_password} onChange={set("confirm_password")} required />
        {mismatch && <span className="field-hint err-text">The passwords do not match.</span>}
        {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
        <button className="btn primary" disabled={busy || mismatch}>{busy && <span className="spinner" />} Create account</button>
        {!after && <p className="muted small">Already have an account? <a href="#/login">Sign in</a></p>}
      </form>
    </AuthFrame>
  );
}

export function Invite({ token, session, onSignedIn, reload, themeButton }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [mode, setMode] = useState("register");
  useEffect(() => {
    api(`/invitations/preview?token=${encodeURIComponent(token)}`).then(setInfo).catch((err) => setError(err.message));
  }, [token]);

  async function accept() {
    setError("");
    try {
      const r = await api("/invitations/accept", { method: "POST", body: { token } });
      setResult(r);
      await reload();
    } catch (err) { setError(err.message); }
  }

  const header = info && (
    <div className="invite-box">
      <span className="chip-icon group-main"><Icon name="user" size={16} /></span>
      <div><strong>{info.organization}</strong><div className="muted small">Role: {info.role} · for {info.email_hint}</div></div>
    </div>
  );
  if (error && !info) return <AuthFrame title="Invitation" themeButton={themeButton}><p className="inline-msg err"><Icon name="alert" size={14} /> {error}</p></AuthFrame>;
  if (!info) return <AuthFrame title="Invitation" themeButton={themeButton}><p className="muted"><span className="spinner" /> Checking the link…</p></AuthFrame>;
  if (info.status !== "pending") {
    return <AuthFrame title="Invitation" themeButton={themeButton}>{header}<p className="inline-msg err"><Icon name="alert" size={14} /> This invitation is {info.status}. Ask the company for a new link.</p></AuthFrame>;
  }
  if (result) {
    return (
      <AuthFrame title="Invitation accepted" themeButton={themeButton}>
        {header}
        <p className="inline-msg ok"><Icon name="check" size={14} /> {result.message}</p>
        <a className="btn primary" href="#/app/overview">Open my workspace</a>
      </AuthFrame>
    );
  }
  if (!session) {
    return (
      <div>
        {mode === "register"
          ? <Register onSignedIn={onSignedIn} themeButton={themeButton} after presetEmail="" />
          : <Login onSignedIn={onSignedIn} themeButton={themeButton} after />}
        <p className="center muted small">You were invited to <b>{info.organization}</b> as {info.role} ({info.email_hint}).{" "}
          <button className="link-btn" onClick={() => setMode(mode === "register" ? "login" : "register")}>
            {mode === "register" ? "I already have an account" : "Create an account instead"}</button></p>
      </div>
    );
  }
  return (
    <AuthFrame title="Join a company" subtitle={`Signed in as ${session.user.email}`} themeButton={themeButton}>
      {header}
      <p className="muted small">The invitation must match your account&apos;s email. After you accept, an owner or admin of the
        company confirms your membership (no email is sent by this app, so they check it is really you).</p>
      {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
      <button className="btn primary" onClick={accept}>Accept invitation</button>
    </AuthFrame>
  );
}

export function SessionEnded({ themeButton }) {
  return (
    <AuthFrame title="Your session has ended" subtitle="You were signed out (or your session expired). Sign in again to continue." themeButton={themeButton}>
      <a className="btn primary" href="#/login">Sign in</a>
    </AuthFrame>
  );
}
