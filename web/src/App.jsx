// The app: public pages (landing, sign in, register, invitation) and, after sign-in, the workspace shell.
// Routes live in the URL hash (#/login, #/invite/<token>, #/app/chat ...). Who the user is comes only from the
// api's /auth/me (the session cookie is HttpOnly); the active workspace and project are UI choices kept in
// localStorage for convenience, and the api checks access on every request anyway.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, setCsrf } from "./api.js";
import { Invite, Login, Register, SessionEnded } from "./AuthPages.jsx";
import Builder from "./canvas/Builder.jsx";
import Chat from "./Chat.jsx";
import Dashboard from "./Dashboard.jsx";
import Icon from "./Icon.jsx";
import Knowledge from "./Knowledge.jsx";
import Landing from "./Landing.jsx";
import Members from "./Members.jsx";
import Overview from "./Overview.jsx";
import Projects from "./Projects.jsx";
import Settings from "./Settings.jsx";
import Status from "./Status.jsx";

function parseHash() {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [first, ...rest] = h.split("/");
  return { page: first || "home", arg: rest.join("/") };
}

export function go(path) { window.location.hash = `#/${path}`; }

function remember(key, value) { try { localStorage.setItem(key, String(value)); } catch { /* private mode */ } }
function recall(key) { try { return localStorage.getItem(key); } catch { return null; } }

function storedTheme() { return recall("theme") || "dark"; }

export default function App() {
  const [session, setSession] = useState(undefined); // undefined while loading, null when signed out
  const [ended, setEnded] = useState(false);
  const [route, setRoute] = useState(parseHash);
  const [theme, setTheme] = useState(storedTheme);

  const loadMe = useCallback(() => api("/auth/me").then((m) => { setCsrf(m.csrf_token); setSession(m); return m; })
    .catch(() => { setCsrf(""); setSession(null); return null; }), []);

  useEffect(() => {
    loadMe();
    const onHash = () => setRoute(parseHash());
    const onEnded = () => { setCsrf(""); setSession(null); setEnded(true); };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("session-ended", onEnded);
    return () => { window.removeEventListener("hashchange", onHash); window.removeEventListener("session-ended", onEnded); };
  }, [loadMe]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    remember("theme", theme);
  }, [theme]);

  const signedIn = (me) => { setCsrf(me.csrf_token); setSession(me); setEnded(false); };

  if (session === undefined) return <div className="boot"><span className="spinner" /> Loading…</div>;
  const themeButton = <ThemeButton theme={theme} setTheme={setTheme} />;

  if (route.page === "invite") return <Invite token={route.arg} session={session} onSignedIn={signedIn} reload={loadMe} themeButton={themeButton} />;
  if (!session) {
    if (ended && route.page === "app") return <SessionEnded themeButton={themeButton} />;
    if (route.page === "login" || route.page === "app") return <Login onSignedIn={signedIn} themeButton={themeButton} />;
    if (route.page === "register") return <Register onSignedIn={signedIn} themeButton={themeButton} />;
    return <Landing themeButton={themeButton} />;
  }
  if (route.page !== "app") { go("app/overview"); return null; }
  return <Shell session={session} screen={route.arg || "overview"} reload={loadMe} themeButton={themeButton}
    onSignedOut={() => { setCsrf(""); setSession(null); go("login"); }} />;
}

function ThemeButton({ theme, setTheme }) {
  return (
    <button className="btn ghost icon-btn sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Switch theme">
      <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
    </button>
  );
}

const SCREENS = [
  ["overview", "Overview", "dashboard"],
  ["projects", "Projects", "folder"],
  ["knowledge", "Knowledge", "knowledge"],
  ["agents", "Agents", "builder"],
  ["chat", "Chat", "chat"],
  ["usage", "Usage", "status"],
  ["members", "Members", "user"],
  ["settings", "Settings", "validate"],
];

function Shell({ session, screen, reload, themeButton, onSignedOut }) {
  const active = session.workspaces.filter((w) => w.status === "active");
  const pending = session.workspaces.filter((w) => w.status === "pending");
  const [orgId, setOrgId] = useState(() => {
    const saved = Number(recall("org"));
    return active.some((w) => w.id === saved) ? saved : active[0]?.id;
  });
  const org = active.find((w) => w.id === orgId) || active[0];
  const [projects, setProjects] = useState(null);
  const [projectId, setProjectId] = useState(() => Number(recall("project")) || null);
  const [drawer, setDrawer] = useState(false);
  const [creating, setCreating] = useState(false);
  const [builderOpened, setBuilderOpened] = useState(null);  // project id whose builder is kept alive across tabs

  const loadProjects = useCallback(() => {
    if (!org) return Promise.resolve();
    // No setProjects(null) here: a refresh after an upload must not unmount the open screen (and lose its
    // message). The list is cleared only when the workspace changes (switchOrg, a new company).
    return api(`/projects?org_id=${org.id}`).then((list) => {
      setProjects(list);
      setProjectId((cur) => (list.some((p) => p.id === cur) ? cur : list.find((p) => !p.archived)?.id ?? null));
    }).catch(() => setProjects([]));
  }, [org?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { loadProjects(); }, [loadProjects]);
  useEffect(() => { if (org) remember("org", org.id); }, [org]);
  useEffect(() => { if (projectId) remember("project", projectId); }, [projectId]);

  const project = projects?.find((p) => p.id === projectId) || null;
  useEffect(() => { if (screen === "agents" && project) setBuilderOpened(project.id); }, [screen, project]);
  const isAdmin = org && ["owner", "admin"].includes(org.role);
  const visible = SCREENS.filter(([key]) => (key === "members" ? org?.kind === "company" : key === "settings" ? true : true));

  async function signOut() {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* already ended */ }
    onSignedOut();
  }

  function switchOrg(value) {
    if (value === "new") { setCreating(true); return; }
    if (Number(value) === org?.id) return;  // same workspace: nothing to reload (and the list must not be cleared)
    setProjects(null);
    setOrgId(Number(value));
    setProjectId(null);
    setDrawer(false);
  }

  const ctx = { session, org, isAdmin, project, projects, setProjectId, reloadProjects: loadProjects, reloadMe: reload };
  const title = SCREENS.find(([k]) => k === screen)?.[1] || "Overview";

  return (
    <div className={`app${drawer ? " drawer-open" : ""}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" width="28" height="28"><defs><linearGradient id="bm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#8b7bff" /><stop offset="1" stopColor="#2fc8a0" /></linearGradient></defs><rect width="32" height="32" rx="9" fill="url(#bm)" /><path d="M9 21V11h6.5a3 3 0 0 1 0 6H9m6.5 0H17a3 3 0 0 1 0 6H9" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" transform="translate(0 -1)" /></svg>
          </span>
          <span className="brand-text"><strong>BharatAgent</strong><span>Studio</span></span>
        </div>

        <label className="field ws-switch">
          <span className="field-label">Workspace</span>
          <select value={org?.id || ""} onChange={(e) => switchOrg(e.target.value)} aria-label="Workspace">
            {active.map((w) => <option key={w.id} value={w.id}>{w.kind === "personal" ? `${w.name} (personal)` : w.name}</option>)}
            <option value="new">+ Create a company…</option>
          </select>
        </label>
        <label className="field ws-switch">
          <span className="field-label">Project</span>
          <select value={project?.id || ""} onChange={(e) => setProjectId(Number(e.target.value))} aria-label="Project"
            disabled={!projects?.length}>
            {!projects?.length && <option value="">{projects ? "No projects yet" : "Loading…"}</option>}
            {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}{p.archived ? " (archived)" : ""}</option>)}
          </select>
        </label>

        <nav>
          {visible.map(([key, label, icon]) => (
            <a key={key} href={`#/app/${key}`} className={`nav-item${screen === key ? " on" : ""}`}
              aria-current={screen === key ? "page" : undefined} onClick={() => setDrawer(false)}>
              <Icon name={icon} size={18} /> <span>{label}</span>
            </a>
          ))}
        </nav>
        {pending.length > 0 && (
          <p className="note small side-note"><Icon name="clock" size={13} /> Waiting for confirmation: {pending.map((w) => w.name).join(", ")}</p>
        )}
        <div className="sidebar-foot">
          <div className="user-chip">
            <span className="avatar sm">{(session.user.full_name || "?").slice(0, 1).toUpperCase()}</span>
            <span className="user-text"><strong>{session.user.full_name}</strong><span className="muted small">{session.user.email}</span></span>
          </div>
          <div className="foot-row">
            <a href="#/app/status" className="health"><Icon name="status" size={13} /> System status</a>
            {themeButton}
            <button className="btn ghost icon-btn sm" onClick={signOut} aria-label="Sign out" title="Sign out"><Icon name="x" size={16} /></button>
          </div>
        </div>
      </aside>
      <button className="scrim" aria-label="Close menu" onClick={() => setDrawer(false)} tabIndex={drawer ? 0 : -1} />

      <div className="main-col">
        <header className="topbar">
          <button className="btn ghost icon-btn" onClick={() => setDrawer(true)} aria-label="Open menu"><Icon name="menu" size={18} /></button>
          <strong>{title}</strong>
          <span className="topbar-user muted small">{org?.name}</span>
        </header>
        <main id="main" className={`main screen-${screen}`}>
          {screen === "overview" && <Overview {...ctx} />}
          {screen === "projects" && <Projects {...ctx} />}
          {screen === "knowledge" && <Gate project={project}><Knowledge {...ctx} /></Gate>}
          {/* The builder stays mounted (only hidden) once opened, so an unsaved or generated agent, its run and its
              canvas survive a visit to another tab. A new project gets a fresh builder (key). */}
          {project && (screen === "agents" || builderOpened === project.id) && (
            <div className="keep-alive" hidden={screen !== "agents"}><Builder key={project.id} {...ctx} /></div>
          )}
          {screen === "agents" && !project && <Gate project={project} />}
          {screen === "chat" && <Gate project={project}><Chat key={project?.id} {...ctx} /></Gate>}
          {screen === "usage" && <Dashboard {...ctx} />}
          {screen === "members" && <Members {...ctx} />}
          {screen === "settings" && <Settings {...ctx} />}
          {screen === "status" && <Status />}
        </main>
      </div>
      {creating && <CreateCompany onClose={() => setCreating(false)} onCreated={async (o) => {
        setCreating(false); await reload(); setProjects(null); setOrgId(o.id); go("app/overview");
      }} />}
    </div>
  );
}

function Gate({ project, children }) {
  if (!project) {
    return (
      <div className="page-pad"><div className="empty">
        <Icon name="folder" size={26} />
        <h3>Choose or create a project first</h3>
        <p className="muted">Knowledge, agents and chats belong to a project.</p>
        <a className="btn primary" href="#/app/projects">Open projects</a>
      </div></div>
    );
  }
  return children;
}

export function Modal({ title, children, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal card" role="dialog" aria-modal="true" aria-label={title}>
        <div className="card-head"><h3>{title}</h3><button className="btn ghost icon-btn sm push" onClick={onClose} aria-label="Close"><Icon name="x" size={16} /></button></div>
        {children}
      </div>
    </div>
  );
}

function CreateCompany({ onClose, onCreated }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError("");
    try { onCreated(await api("/orgs", { method: "POST", body: { name, description } })); }
    catch (err) { setError(err.message); setBusy(false); }
  }
  return (
    <Modal title="Create a company" onClose={onClose}>
      <form className="stack form" onSubmit={submit}>
        <label className="field"><span className="field-label">Company name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} autoFocus /></label>
        <label className="field"><span className="field-label">Description (optional)</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={1000} /></label>
        {error && <p className="inline-msg err" role="alert"><Icon name="alert" size={14} /> {error}</p>}
        <p className="muted small">You become its owner. You can invite colleagues from Members.</p>
        <button className="btn primary" disabled={busy || !name.trim()}>{busy ? <span className="spinner" /> : null} Create company</button>
      </form>
    </Modal>
  );
}

export function useConfirm() {
  const [state, setState] = useState(null);
  const ask = useCallback((message, action, label = "Delete") => setState({ message, action, label }), []);
  const dialog = useMemo(() => state && (
    <Modal title="Please confirm" onClose={() => setState(null)}>
      <p>{state.message}</p>
      <div className="row-end">
        <button className="btn ghost" onClick={() => setState(null)}>Cancel</button>
        <button className="btn danger" onClick={async () => { const a = state.action; setState(null); await a(); }}>{state.label}</button>
      </div>
    </Modal>
  ), [state]);
  return [ask, dialog];
}
