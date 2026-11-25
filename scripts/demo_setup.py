"""One-click demo: a demo account with a ready project, its knowledge and three saved agents.

Run it with scripts/demo-setup.ps1 (it runs this file inside the api image, on the api container's network).
Safe to run again: it signs in if the account exists, and skips sources and agents that are already there, so
after a broken demo you just run it once more. The account's email and password live in .env.demo-account
(git-ignored); a password is generated the first time and never printed.

What it creates, in the demo account's personal workspace, project "BharatAgent demo":
  knowledge  refund-policy.pdf (clinic), hostel-rules.pdf (college), support-runbook.md (support desk)
  agents     Clinic refund helper (Telugu)    refund PDF + calculator
             College hostel helper (Hindi)    hostel rules PDF
             Pharmacy front desk (approvals)  refund PDF + orders tool that may cancel orders, with approval
"""

import secrets
import sys
from pathlib import Path

import httpx

ROOT = Path("/w")
API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
CREDS = ROOT / ".env.demo-account"
PROJECT = "BharatAgent demo"
FILES = [ROOT / "samples" / "refund-policy.pdf", ROOT / "samples" / "hostel-rules.pdf",
         ROOT / "samples" / "knowledge" / "support-runbook.md"]
MAIN = [("n1", "input"), ("n2", "guardrails"), ("n3", "router"), ("n4", "agent"), ("n5", "output")]


def load_creds() -> dict:
    if CREDS.exists():
        pairs = (line.strip().split("=", 1) for line in CREDS.read_text(encoding="utf-8-sig").splitlines()
                 if "=" in line and not line.lstrip().startswith("#"))
        creds = {k.strip(): v.strip() for k, v in pairs}
        if creds.get("DEMO_EMAIL") and creds.get("DEMO_PASSWORD"):
            return creds
    creds = {"DEMO_EMAIL": "demo.builder@example.test", "DEMO_PASSWORD": "Demo" + secrets.token_urlsafe(14) + "7"}
    CREDS.write_text("# Local demo account for scripts/demo-setup.ps1. Git-ignored; do not commit.\n"
                     + "".join(f"{k}={v}\n" for k, v in creds.items()), encoding="utf-8")
    print(f"Created {CREDS.name} with a new demo password (not printed).")
    return creds


def sign_in(client: httpx.Client, creds: dict) -> None:
    r = client.post("/auth/login", json={"email": creds["DEMO_EMAIL"], "password": creds["DEMO_PASSWORD"]})
    if r.status_code in (400, 401, 404):
        r = client.post("/auth/register", json={"full_name": "Demo Builder", "email": creds["DEMO_EMAIL"],
                                                "password": creds["DEMO_PASSWORD"], "confirm_password": creds["DEMO_PASSWORD"]})
        print("Registered the demo account." if r.is_success else "")
    if not r.is_success:
        sys.exit(f"Could not sign in or register the demo account: HTTP {r.status_code} {r.text[:200]}")
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]


def graph(language: str, instructions: str, sources: list[int], tools: list[dict]) -> dict:
    nodes = [{"id": nid, "type": t, "config": {}} for nid, t in MAIN]
    nodes[1]["config"] = {"block": ["empty", "abuse", "off_topic"]}
    nodes[3]["config"] = {"model": "groq", "language": language, "local_only": False, "instructions": instructions}
    edges = [{"from": a[0], "to": b[0]} for a, b in zip(MAIN, MAIN[1:])]
    for i, sid in enumerate(sources, start=1):
        nodes.append({"id": f"src{i}", "type": "source_doc", "config": {"source_ref": sid}})
        edges.append({"from": f"src{i}", "to": "n4"})
    for i, cfg in enumerate(tools, start=1):
        nodes.append({"id": f"tool{i}", "type": cfg.pop("type"), "config": cfg})
        edges.append({"from": f"tool{i}", "to": "n4"})
    return {"nodes": nodes, "edges": edges}


def main() -> None:
    creds = load_creds()
    client = httpx.Client(base_url=API, timeout=600)
    sign_in(client, creds)
    me = client.get("/auth/me").json()
    org = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")

    project = next((p for p in client.get("/projects", params={"org_id": org}).json() if p["name"] == PROJECT), None)
    if project is None:
        r = client.post("/projects", json={"org_id": org, "name": PROJECT, "description":
                        "Ready-made demo: a clinic, a college and a pharmacy desk, with their documents and agents."})
        r.raise_for_status()
        project = r.json()
        print(f"Created project '{PROJECT}'.")
    pid = project["id"]

    have = {s["title"]: s for s in client.get(f"/projects/{pid}/sources").json()}
    ids = {}
    for path in FILES:
        src = have.get(path.name)
        if src is None or src.get("status") != "ready":
            with path.open("rb") as f:
                r = client.post(f"/projects/{pid}/sources/file", files={"file": (path.name, f)})
            r.raise_for_status()
            src = r.json()
            print(f"Indexed {path.name}: {src.get('status')}, {src.get('chunk_count')} chunks.")
        ids[path.name] = src["id"]

    agents = {
        "Clinic refund helper (Telugu)": graph(
            "te", "You help patients of a small clinic with refund and cancellation questions. Answer simply and politely.",
            [ids["refund-policy.pdf"]], [{"type": "tool_calculator"}]),
        "College hostel helper (Hindi)": graph(
            "hi", "You help students and parents with hostel rules, timings and fees. Be friendly and clear.",
            [ids["hostel-rules.pdf"]], []),
        "Pharmacy front desk (approvals)": graph(
            "en", "You are the pharmacy front desk. Look up orders, and cancel an order only when the patient asks.",
            [ids["refund-policy.pdf"]], [{"type": "tool_mcp", "server": "orders-db", "mode": "read_write", "allow_write": True}]),
    }
    saved = {g["name"] for g in client.get("/graphs", params={"project_id": pid}).json()}
    for name, g in agents.items():
        if name in saved:
            continue
        r = client.post("/graphs", json={"project_id": pid, "name": name, "graph": g})
        if not r.is_success:
            sys.exit(f"Could not save agent '{name}': HTTP {r.status_code} {r.text[:300]}")
        print(f"Saved agent '{name}' (#{r.json()['id']}).")

    print()
    print("Demo ready. Open http://localhost:3000 and sign in as", creds["DEMO_EMAIL"])
    print("(password: in .env.demo-account), then pick the project:", PROJECT)
    print("Try in Chat, choosing an agent:")
    print("  Clinic refund helper:   lab test cancel chesthe refund enta vastundi?")
    print("  College hostel helper:  हॉस्टल का गेट कितने बजे बंद होता है?")
    print("  Pharmacy front desk:    Please cancel order ORD-1001.   (then Approve or Deny)")


if __name__ == "__main__":
    main()
