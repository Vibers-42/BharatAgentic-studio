// App shell: a title, the user switcher ("usage per person" needs a person), and two tabs:
// Chat (ask questions over your PDF) and Status (are the containers up).
import { useEffect, useState } from "react";
import Chat from "./Chat.jsx";
import Status from "./Status.jsx";

export default function App() {
  const [tab, setTab] = useState("chat");
  const [users, setUsers] = useState([]);
  const [user, setUser] = useState("demo");

  useEffect(() => {
    fetch("/api/users")
      .then((r) => r.json())
      .then(setUsers)
      .catch(() => {});
  }, []);

  return (
    <main className="page">
      <header className="top">
        <h1>BharatAgent Studio</h1>
        <nav>
          <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="User">
            {(users.length ? users : [{ name: "demo" }]).map((u) => (
              <option key={u.name} value={u.name}>{u.name}</option>
            ))}
          </select>
          <button className={tab === "chat" ? "tab on" : "tab"} onClick={() => setTab("chat")}>Chat</button>
          <button className={tab === "status" ? "tab on" : "tab"} onClick={() => setTab("status")}>Status</button>
        </nav>
      </header>
      {tab === "chat" ? <Chat user={user} /> : <Status />}
    </main>
  );
}
