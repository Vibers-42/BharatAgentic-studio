// App shell: a title, the user switcher ("usage per person" needs a person), and four tabs:
// Builder (draw and run the agent pipeline), Chat (quick questions over your sources),
// Dashboard (usage and cost per user and model) and Status (are the containers up).
import { useEffect, useState } from "react";
import Builder from "./canvas/Builder.jsx";
import Chat from "./Chat.jsx";
import Dashboard from "./Dashboard.jsx";
import Status from "./Status.jsx";

const TABS = [["builder", "Builder"], ["chat", "Chat"], ["dashboard", "Dashboard"], ["status", "Status"]];

export default function App() {
  const [tab, setTab] = useState("builder");
  const [users, setUsers] = useState([]);
  const [user, setUser] = useState("demo");

  useEffect(() => {
    fetch("/api/users")
      .then((r) => r.json())
      .then(setUsers)
      .catch(() => {});
  }, []);

  return (
    <main className={tab === "builder" ? "page wide" : "page"}>
      <header className="top">
        <h1>BharatAgent Studio</h1>
        <nav>
          <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="User">
            {(users.length ? users : [{ name: "demo" }]).map((u) => (
              <option key={u.name} value={u.name}>{u.name}</option>
            ))}
          </select>
          {TABS.map(([key, label]) => (
            <button key={key} className={tab === key ? "tab on" : "tab"} onClick={() => setTab(key)}>{label}</button>
          ))}
        </nav>
      </header>
      {tab === "builder" && <Builder user={user} />}
      {tab === "chat" && <Chat user={user} />}
      {tab === "dashboard" && <Dashboard />}
      {tab === "status" && <Status />}
    </main>
  );
}
