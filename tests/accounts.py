"""Test helpers for Phase 8: real accounts made through the real /auth/register route. Each Account has its own
TestClient, so its own cookie jar (session) and its own CSRF header: two Accounts are two different people,
never one client with a different user id."""

import uuid

import numpy as np
from fastapi.testclient import TestClient

PASSWORD = "Correct-horse-42"
WORDS = ["refund", "hostel", "clinic", "readme", "canteen", "architecture", "payment", "order"]


def vector_for(text: str) -> list[float]:
    """Deterministic fake embedding: texts about the same topic word point the same way."""
    v = np.zeros(1024, dtype=np.float32)
    for i, word in enumerate(WORDS):
        if word in text.lower():
            v[i] = 1.0
    v[1023] = 0.1
    return list(v / np.linalg.norm(v))


def fake_embeddings(monkeypatch):
    """Fake bge-m3 but the real retrieve(), so searches really hit Postgres."""
    from app.pipeline.stages import agent
    from app.rag import embeddings
    from app.rag.retrieve import retrieve
    monkeypatch.setattr(embeddings, "embed", lambda texts: [vector_for(t) for t in texts])
    monkeypatch.setattr(embeddings, "embed_one", vector_for)
    monkeypatch.setattr(agent, "retrieve", retrieve)


class Account:
    def __init__(self, app, label: str):
        self.email = f"{label}-{uuid.uuid4().hex[:8]}@example.test"
        self.name = label.replace("_", " ").title()
        self.c = TestClient(app)
        r = self.c.post("/auth/register", json={"full_name": self.name, "email": self.email, "password": PASSWORD,
                                                "confirm_password": PASSWORD})
        assert r.status_code == 201, r.text
        self.me = r.json()
        self.id = self.me["user"]["id"]
        self.c.headers["X-CSRF-Token"] = self.me["csrf_token"]
        self.personal = next(w["id"] for w in self.me["workspaces"] if w["kind"] == "personal")
        self.project = self.c.get(f"/projects?org_id={self.personal}").json()[0]["id"]

    def __getattr__(self, name):  # get, post, put, patch, delete, stream
        return getattr(self.c, name)

    def add_pdf(self, project_id: int, name: str, data: bytes):
        return self.c.post(f"/projects/{project_id}/sources/file", files={"file": (name, data, "application/pdf")})

    def ask(self, **body):
        r = self.c.post("/pipeline/run?stream=false", json=body)
        return r, (r.json()["events"] if r.status_code == 200 else None)


def agent_graph(sources: dict[str, int] | None = None, tools: list[dict] | None = None, model: str = "gemini",
                local_only: bool = False) -> dict:
    """The five blocks plus source nodes pointing at registry sources ({node id: source id}) and tools."""
    main = ["input", "guardrails", "router", "agent", "output"]
    nodes = [{"id": f"n{i + 1}", "type": t, "config": ({"model": model, "language": "en", "local_only": local_only}
                                                         if t == "agent" else {})} for i, t in enumerate(main)]
    edges = [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(1, 5)]
    for node, sid in (sources or {}).items():
        nodes.append({"id": node, "type": "source_doc", "config": {"source_ref": sid}})
        edges.append({"from": node, "to": "n4"})
    for t in tools or []:
        nodes.append(t)
        edges.append({"from": t["id"], "to": "n4"})
    return {"nodes": nodes, "edges": edges}
