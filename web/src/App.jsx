// App shell: a sidebar with the five screens (Builder, Chat, Knowledge, Usage, Status), the user switcher
// ("usage per person" needs a person) and a light/dark switch. On small screens the sidebar becomes a
// drawer opened from the top bar. The screen is kept in the URL hash, so reloads and links keep it.
import { useEffect, useState } from "react";
import { api } from "./api.js";
import Builder from "./canvas/Builder.jsx";
import Chat from "./Chat.jsx";
import Dashboard from "./Dashboard.jsx";
import Icon from "./Icon.jsx";
import Knowledge from "./Knowledge.jsx";
import Status from "./Status.jsx";

const SCREENS = [
  ["builder", "Builder", "builder"],
  ["chat", "Chat", "chat"],
  ["knowledge", "Knowledge", "knowledge"],
  ["dashboard", "Usage", "dashboard"],
  ["status", "Status", "status"],
];

function readHash() {
  const h = window.location.hash.replace("#", "");
  return SCREENS.some(([k]) => k === h) ? h : "builder";
}

function storedTheme() {
  try { return localStorage.getItem("theme") || "dark"; } catch { return "dark"; }
}

export default function App() {
  const [screen, setScreen] = useState(readHash);
  const [users, setUsers] = useState([]);
  const [user, setUser] = useState("demo");
  const [theme, setTheme] = useState(storedTheme);
  const [drawer, setDrawer] = useState(false);
  const [online, setOnline] = useState(null);

  useEffect(() => {
    api("/users").then(setUsers).catch(() => {});
    api("/status").then((s) => setOnline(s.api && s.db && s.ollama)).catch(() => setOnline(false));
    const onHash = () => setScreen(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch { /* private mode */ }
  }, [theme]);

  function go(key) {
    window.location.hash = key;
    setScreen(key);
    setDrawer(false);
  }

  const title = SCREENS.find(([k]) => k === screen)?.[1];

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
        <nav>
          {SCREENS.map(([key, label, icon]) => (
            <a key={key} href={`#${key}`} className={`nav-item${screen === key ? " on" : ""}`} aria-current={screen === key ? "page" : undefined}
              onClick={(e) => { e.preventDefault(); go(key); }}>
              <Icon name={icon} size={18} /> <span>{label}</span>
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          <label className="field">
            <span className="field-label"><Icon name="user" size={13} /> Signed in as</span>
            <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="User">
              {(users.length ? users : [{ name: "demo" }]).map((u) => <option key={u.name} value={u.name}>{u.name}</option>)}
            </select>
          </label>
          <div className="foot-row">
            <span className={`health ${online === null ? "" : online ? "up" : "down"}`} title="api, database and local models">
              <span className={`dot ${online === null ? "" : online ? "up" : "down"}`} />{online === null ? "Checking…" : online ? "All systems local" : "Service down"}
            </span>
            <button className="btn ghost icon-btn sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Switch theme">
              <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
            </button>
          </div>
        </div>
      </aside>
      <button className="scrim" aria-label="Close menu" onClick={() => setDrawer(false)} tabIndex={drawer ? 0 : -1} />

      <div className="main-col">
        <header className="topbar">
          <button className="btn ghost icon-btn" onClick={() => setDrawer(true)} aria-label="Open menu"><Icon name="menu" size={18} /></button>
          <strong>{title}</strong>
          <span className="topbar-user muted small"><Icon name="user" size={13} /> {user}</span>
        </header>
        <main id="main" className={`main screen-${screen}`}>
          {/* The builder stays mounted (hidden) on other screens, so unsaved canvas work is kept. */}
          <div className="builder-host" hidden={screen !== "builder"}><Builder user={user} /></div>
          {screen === "chat" && <Chat user={user} go={go} />}
          {screen === "knowledge" && <Knowledge go={go} />}
          {screen === "dashboard" && <Dashboard />}
          {screen === "status" && <Status />}
        </main>
      </div>
    </div>
  );
}
