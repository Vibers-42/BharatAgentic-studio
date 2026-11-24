"""Phase 8 acceptance: all eight knowledge source types, shared between two employees, end to end on the
running stack with real models (no mocks).

  1. An owner creates a company and a project, invites employee A (contributor) and employee B (viewer), and
     confirms both. A fourth account (outsider) is never invited.
  2. Employee A adds eight sources, each holding one distinct, known fact: PDF (samples/ecommerce-architecture.pdf),
     public GitHub repo (octocat/Spoon-Knife), docs URL (https://example.com), and TXT, Markdown, DOCX, CSV and
     XLSX from samples/knowledge/ (make_knowledge_samples.py).
  3. For every source the database is checked: status ready, chunk count, a vector for every chunk (1024
     dimensions, bge-m3), and that the extracted chunk text contains the fact.
  4. The owner saves one shared agent that uses all eight sources (nothing is uploaded again).
  5. Employee B asks one question per source in project chat and through the shared agent, each on Groq
     (openai/gpt-oss-120b) and Qwen 3 14B: the answer must contain the fact and cite that source.
  6. B downloads every uploaded file (same bytes); the outsider gets 404 for every source, file and run.
     No chunk row is created, removed or re-embedded by any of this (chunk ids compared before and after).
  7. A streaming answer is cut when B is removed from the project mid-stream (access_revoked); a stream the
     client abandons leaves no completed answer in the conversation.
  8. The whole stack is restarted (docker compose restart, volumes kept); B signs in again with a new client,
     the same sources and chunks are there, and every source still answers (Groq, project chat).

Writes docs/results/phase8_sources.json and prints PASS / FAIL per check; exit code 1 if anything failed.
Passwords are random for this run, kept in memory only, never printed.

  python scripts/verify_phase8_sources.py
"""

import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _session import sign_in, sign_up  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
API = "http://127.0.0.1:8000"
OUT = ROOT / "docs" / "results" / "phase8_sources.json"
K = ROOT / "samples" / "knowledge"

# node id -> (kind, how it is added, question, answer must match, cited title must match)
SOURCES = {
    "s_pdf": ("file", ROOT / "samples" / "ecommerce-architecture.pdf", "Above what order value is delivery free?",
              r"499", r"ecommerce-architecture\.pdf"),
    "s_repo": ("repo", "https://github.com/octocat/Spoon-Knife", "What is the Spoon-Knife repository meant for?",
               r"(?i)fork|pull request", r"README\.md"),
    "s_url": ("url", "https://example.com", "What is the example.com domain for?",
              r"(?i)example|illustrat|documentation", r"Example Domain"),
    "s_txt": ("file", K / "warehouse-notes.txt", "Until what time does the Hyderabad warehouse ship orders on weekdays?",
              r"6:30", r"warehouse-notes\.txt"),
    "s_md": ("file", K / "support-runbook.md", "Within how many minutes must a priority-1 support ticket get a first response?",
             r"\b15\b", r"support-runbook\.md"),
    "s_docx": ("file", K / "leave-policy.docx", "How many days of paid leave do employees get each year?",
               r"\b18\b", r"leave-policy\.docx"),
    "s_csv": ("file", K / "courier-partners.csv", "What is the maximum parcel weight that the courier SwiftShip carries?",
              r"\b32\b", r"courier-partners\.csv"),
    "s_xlsx": ("file", K / "gift-cards.xlsx", "For how many months is the Festive gift card valid?",
               r"\b9\b", r"gift-cards\.xlsx"),
}
FACT_IN_CHUNKS = {"s_pdf": "Rs. 499", "s_repo": "fork", "s_url": "documentation examples", "s_txt": "6:30 PM",
                  "s_md": "15 minutes", "s_docx": "18 days", "s_csv": "(kg): 32", "s_xlsx": "Validity (months): 9"}
MODELS = {"groq": "groq:", "local": "local:qwen3:14b"}

results: list[dict] = []
evidence: dict = {"sources": {}, "answers": [], "restart": {}}


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"status": "PASS" if ok else "FAIL", "check": name, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{f' - {detail}' if detail else ''}", flush=True)
    return ok


def sql(query: str) -> str:
    r = subprocess.run(["docker", "compose", "exec", "-T", "db", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'],
                       input=query, capture_output=True, text=True, cwd=ROOT, timeout=60)
    if r.returncode != 0 or "ERROR" in r.stderr:
        raise RuntimeError(f"SQL failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def wait_api(seconds: int = 300) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if httpx.get(f"{API}/health", timeout=3).json().get("ok"):
                return True
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(2)
    return False


def run(client: httpx.Client, **body) -> tuple[int, list]:
    r = client.post("/pipeline/run?stream=false", json=body)
    return r.status_code, (r.json()["events"] if r.status_code == 200 else [])


def ask_and_judge(client, mode: str, model: str, node: str, sid: int, agent_id: int, title: str) -> dict:
    kind, _, question, fact, cited_title = SOURCES[node]
    body = {"question": question, "model": model}
    body.update({"graph_id": agent_id} if mode == "agent" else {"project_id": evidence["project"]})
    started = time.time()
    status, events = run(client, **body)
    last = events[-1] if events else {}
    want_cite = node if mode == "agent" else f"source_{sid}"
    cites = last.get("citations") or []
    cited = [c for c in cites if c.get("source_id") == want_cite]
    row = {"source": node, "source_id": sid, "mode": mode, "model": model, "question": question,
           "expected": fact, "answer": (last.get("answer") or last.get("message") or "")[:300],
           "answered_by": last.get("model"), "supported": last.get("supported"),
           "cited_files": sorted({c.get("file") for c in cited}), "seconds": round(time.time() - started, 1)}
    row["ok"] = (status == 200 and last.get("type") == "result" and bool(last.get("supported"))
                 and str(last.get("model", "")).startswith(MODELS[model]) and not last.get("fallback")
                 and re.search(fact, row["answer"]) is not None and any(re.search(cited_title, f or "") for f in row["cited_files"]))
    evidence["answers"].append(row)
    return row


def main():  # noqa: C901 - one linear scenario
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not wait_api(30):
        print("The api is not reachable: this run is NOT valid. Start Docker Desktop and the stack first.")
        return 2

    # ---- people, company, project ----
    owner, emp_a, emp_b, outsider = (sign_up(API, k) for k in ("src-owner", "src-employee-a", "src-employee-b", "src-outsider"))
    o = owner.client
    org = o.post("/orgs", json={"name": f"Source Check Co {int(time.time())}"}).json()["id"]
    proj = o.post("/projects", json={"org_id": org, "name": "Shared knowledge check"}).json()["id"]
    evidence.update(org=org, project=proj)
    for emp, role in ((emp_a, "contributor"), (emp_b, "viewer")):
        token = o.post(f"/orgs/{org}/invitations", json={"email": emp.email, "role": "member"}).json()["token"]
        assert emp.client.post("/invitations/accept", json={"token": token}).status_code == 200
        assert o.post(f"/orgs/{org}/members/{emp.user_id}/confirm").status_code == 200
        assert o.put(f"/projects/{proj}/members/{emp.user_id}", json={"role": role}).status_code == 200
    check("company, project and two confirmed employees (A contributor, B viewer)", True, f"org {org}, project {proj}")

    # ---- employee A adds all eight sources ----
    ids: dict[str, int] = {}
    hashes: dict[str, str] = {}
    for node, (kind, where, *_rest) in SOURCES.items():
        if kind == "file":
            data = where.read_bytes()
            hashes[node] = hashlib.sha256(data).hexdigest()
            r = emp_a.client.post(f"/projects/{proj}/sources/file", files={"file": (where.name, data, "application/octet-stream")})
        else:
            r = emp_a.client.post(f"/projects/{proj}/sources/{kind}", json={"url": where})
        ok = r.status_code == 201 and r.json().get("status") == "ready"
        if ok:
            ids[node] = r.json()["id"]
        check(f"employee A adds {node} ({kind}: {getattr(where, 'name', where)})", ok,
              f"source #{r.json().get('id')} {r.json().get('title')}, {r.json().get('chunk_count')} chunks" if ok else f"HTTP {r.status_code} {r.text[:150]}")
    if len(ids) != len(SOURCES):
        return finish()

    # ---- database evidence per source ----
    for node, sid in ids.items():
        row = sql(f"SELECT count(*), count(embedding), coalesce(min(vector_dims(embedding)), 0), "
                  f"bool_or(content ILIKE '%{FACT_IN_CHUNKS[node].replace(chr(39), '')}%'), "
                  f"(SELECT s.status || '/' || s.chunk_count || '/' || coalesce(p.full_name, p.name) FROM sources s JOIN users p ON p.id = s.uploaded_by WHERE s.id = {sid}) "
                  f"FROM chunks WHERE source_ref = {sid}").split("|")
        n, emb, dims, has_fact, meta = int(row[0]), int(row[1]), int(row[2]), row[3] == "t", row[4]
        evidence["sources"][node] = {"source_id": sid, "chunks": n, "embeddings": emb, "dims": dims,
                                     "fact_in_chunks": has_fact, "status/chunk_count/uploader": meta}
        check(f"{node}: extracted, chunked, embedded and stored in PostgreSQL", n > 0 and emb == n and dims == 1024 and has_fact
              and meta.startswith(f"ready/{n}/"), f"source #{sid}: {n} chunks, {emb} vectors of {dims} dims, fact in text: {has_fact}")
    before = sql(f"SELECT source_ref || ':' || array_to_string(array_agg(id ORDER BY id), ',') FROM chunks "
                 f"WHERE source_ref IN ({','.join(map(str, ids.values()))}) GROUP BY source_ref ORDER BY source_ref")

    # ---- the owner's shared agent over the same sources ----
    main_nodes = ["input", "guardrails", "router", "agent", "output"]
    nodes = [{"id": f"n{i + 1}", "type": t, "config": {"model": "local", "language": "en"} if t == "agent" else {}}
             for i, t in enumerate(main_nodes)]
    nodes += [{"id": n, "type": "source_repo" if SOURCES[n][0] == "repo" else "source_doc", "config": {"source_ref": sid}}
              for n, sid in ids.items()]
    edges = [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(1, 5)] + [{"from": n, "to": "n4"} for n in ids]
    r = o.post("/graphs", json={"project_id": proj, "name": "All sources agent", "graph": {"nodes": nodes, "edges": edges}})
    agent_id = r.json().get("id")
    count = int(sql(f"SELECT count(*) FROM sources WHERE project_id = {proj}"))
    check("owner saves one shared agent using all eight project sources (no new sources)", r.status_code == 201 and count == 8,
          f"agent #{agent_id}, project sources {count}")

    # ---- employee B: sees the knowledge, cannot add, asks every source ----
    b = emp_b.client
    listed = {s["id"] for s in b.get(f"/projects/{proj}/sources").json()}
    check("employee B lists the same eight sources (ids match) and cannot upload (viewer)",
          listed == set(ids.values()) and b.post(f"/projects/{proj}/sources/url", json={"url": "https://example.com"}).status_code == 403,
          f"B sees {sorted(listed)}")
    for mode in ("project chat", "agent"):
        for model in ("groq", "local"):
            for node, sid in ids.items():
                row = ask_and_judge(b, mode, model, node, sid, agent_id, SOURCES[node][4])
                check(f"B asks {node} via {mode} on {model}: correct and cited", row["ok"],
                      f"{row['answered_by']} {row['seconds']}s cites {row['cited_files']}: {row['answer'][:110]}")

    # ---- downloads, preview, outsider ----
    for node, sid in ids.items():
        if SOURCES[node][0] != "file":
            continue
        d = b.get(f"/sources/{sid}/file")
        same = d.status_code == 200 and hashlib.sha256(d.content).hexdigest() == hashes[node]
        evidence["sources"][node]["download"] = {"status": d.status_code, "same_bytes": same,
                                                 "content_disposition": d.headers.get("content-disposition")}
        check(f"{node}: employee B downloads the original file (same SHA-256)", same, d.headers.get("content-disposition", ""))
    x = outsider.client
    refused = {node: (x.get(f"/sources/{sid}").status_code, x.get(f"/sources/{sid}/file").status_code) for node, sid in ids.items()}
    runs = [run(x, question="delivery", project_id=proj)[0], run(x, question="delivery", graph_id=agent_id)[0],
            x.get(f"/projects/{proj}/sources").status_code, x.get(f"/graphs/{agent_id}").status_code]
    check("outsider: every source, file, listing, agent and run is refused (404)",
          all(v == (404, 404) for v in refused.values()) and runs == [404, 404, 404, 404], json.dumps({"sources": refused, "other": runs}))

    after = sql(f"SELECT source_ref || ':' || array_to_string(array_agg(id ORDER BY id), ',') FROM chunks "
                f"WHERE source_ref IN ({','.join(map(str, ids.values()))}) GROUP BY source_ref ORDER BY source_ref")
    check("no chunk was added, removed or re-embedded by sharing, the agent or 32 questions (same chunk ids)", before == after and bool(before))

    # ---- revocation while an answer streams; an abandoned stream ----
    events: list = []

    def stream_b():
        with b.stream("POST", "/pipeline/run", json={"question": "Summarise the leave policy and the courier partners in detail.",
                                                       "graph_id": agent_id, "model": "local"}, timeout=600) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
    t = threading.Thread(target=stream_b)
    t.start()
    end = time.time() + 300
    while time.time() < end and not any(e.get("type") == "token" for e in events) and t.is_alive():
        time.sleep(0.2)
    removed = o.delete(f"/projects/{proj}/members/{emp_b.user_id}").status_code
    t.join(600)
    last = events[-1] if events else {}
    check("B removed from the project while an answer streams: the stream ends with access_revoked, no result",
          removed == 200 and last.get("code") == "access_revoked" and not any(e.get("type") == "result" for e in events),
          f"tokens before removal: {sum(e.get('type') == 'token' for e in events)}, last event {last.get('type')} {last.get('code')}")
    after_rm = [b.get(f"/projects/{proj}/sources").status_code, b.get(f"/sources/{ids['s_pdf']}/file").status_code,
                run(b, question="delivery", graph_id=agent_id)[0], run(b, question="delivery", project_id=proj)[0]]
    check("after removal (still signed in): listing, file, agent run and project chat are refused", after_rm == [404, 404, 404, 404],
          str(after_rm))
    o.put(f"/projects/{proj}/members/{emp_b.user_id}", json={"role": "viewer"})

    conv = b.post("/conversations", json={"project_id": proj, "graph_id": agent_id, "title": "abandoned"}).json()["id"]
    # A long answer from the local model, so the client really leaves in the middle of it (a question no source
    # matches would finish in one burst before the client could leave).
    usage0 = int(sql("SELECT count(*) FROM usage"))
    seen = []
    with b.stream("POST", "/pipeline/run", json={"question": "Explain the leave policy, the courier partners and the warehouse "
                                                   "shipping hours in full detail, with every number.", "conversation_id": conv,
                                                   "model": "local"}, timeout=600) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                seen.append(json.loads(line[6:])["type"])
                if seen[-1] == "token":
                    break  # the client goes away mid-answer
    time.sleep(90)
    msgs = b.get(f"/conversations/{conv}").json()["messages"]
    usage_added = int(sql("SELECT count(*) FROM usage")) - usage0
    check("an answer abandoned mid-stream is not stored as a completed answer (and generation stops)",
          "result" not in seen and [m["role"] for m in msgs] == ["user"] and usage_added == 0,
          f"messages {[(m['role'], m['status']) for m in msgs]}, usage rows added {usage_added}")
    kept = b.post("/conversations", json={"project_id": proj, "title": "before restart"}).json()["id"]
    run(b, question=SOURCES["s_docx"][2], conversation_id=kept, model="groq")

    # ---- full restart, then sign in again ----
    subprocess.run(["docker", "compose", "restart"], cwd=ROOT, capture_output=True, timeout=600)
    up = wait_api(300)
    time.sleep(5)
    b2 = sign_in(API, emp_b)
    again = {s["id"]: s["chunk_count"] for s in b2.get(f"/projects/{proj}/sources").json()}
    after_restart = sql(f"SELECT source_ref || ':' || array_to_string(array_agg(id ORDER BY id), ',') FROM chunks "
                        f"WHERE source_ref IN ({','.join(map(str, ids.values()))}) GROUP BY source_ref ORDER BY source_ref")
    history = b2.get(f"/conversations/{kept}").json().get("messages", [])
    check("after a full stack restart B signs in again: same 8 sources, same chunks, the conversation is still there",
          up and set(again) == set(ids.values()) and after_restart == before and len(history) == 2,
          f"sources {len(again)}, chunks same: {after_restart == before}, conversation messages {len(history)}")
    for node, sid in ids.items():
        row = ask_and_judge(b2, "project chat", "groq", node, sid, agent_id, SOURCES[node][4])
        evidence["restart"][node] = row["ok"]
        check(f"after restart: {node} still answers correctly with its citation (Groq, project chat)", row["ok"],
              f"{row['answer'][:110]}")
    return finish()


def finish() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"run": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results, "evidence": evidence},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    passed = sum(r["status"] == "PASS" for r in results)
    print(f"\n{passed} passed, {len(results) - passed} failed -> {OUT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
