// App shell: a title and two tabs, Chat (ask questions over your PDF) and Status (are the containers up).
import { useState } from "react";
import Chat from "./Chat.jsx";
import Status from "./Status.jsx";

export default function App() {
  const [tab, setTab] = useState("chat");
  return (
    <main className="page">
      <header className="top">
        <h1>BharatAgent Studio</h1>
        <nav>
          <button className={tab === "chat" ? "tab on" : "tab"} onClick={() => setTab("chat")}>Chat</button>
          <button className={tab === "status" ? "tab on" : "tab"} onClick={() => setTab("status")}>Status</button>
        </nav>
      </header>
      {tab === "chat" ? <Chat /> : <Status />}
    </main>
  );
}
