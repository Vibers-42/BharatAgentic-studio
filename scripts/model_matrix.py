"""Phase 7.1 model verification: the same nine questions on every selectable model, through the real api
and the real providers (no mocks). One saved agent holds a PDF, a GitHub repo, a docs page, the Calculator
and the MCP tool; every question runs against it with the model forced per request.

For each answer it records the requested model, the model that actually answered, the provider, any
fallback and its reason, time, tokens, cost, whether the expected fact is in the answer, whether the
expected source is cited, the tool calls, and whether the answer is in the question's language.
A fallback answer never counts as a pass for the requested model. A Gemini daily quota (HTTP 429) marks
that model's remaining questions BLOCKED and no further Gemini requests are sent.

  python scripts/model_matrix.py                      # all four models
  python scripts/model_matrix.py --models local groq  # some of them
Writes docs/results/model_matrix.json and prints a table. Needs the stack running and internet access
for the GitHub repo and the docs page.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _session import TestAccount, add_file, add_link, sign_up  # noqa: E402  (Phase 8: a real signed-in account)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "results" / "model_matrix.json"
PROVIDER = {"gemini": "Google Gemini", "groq": "Groq", "local": "Ollama", "local_small": "Ollama"}
DONT_KNOW = ("don't know", "తెలియదు", "नहीं पता", "पता नहीं", "జవాబు లేదు")

TESTS = [
    # id, question, language, expected facts (any), expected cited source (or None), expected tool
    ("pdf", "How much is refunded if a lab test is cancelled before the sample is collected?", "en", ["80"], "s_pdf", None),
    ("repo", "What is the Spoon-Knife repository meant for?", "en", ["fork"], "s_repo", None),
    ("url", "What is the example.com domain for?", "en", ["example", "illustrat"], "s_url", None),
    ("calculator", "My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?",
     "en", ["960"], "t_calc", "calculator"),
    ("mcp", "What is the status and refund of order ORD-1002?", "en", ["960", "cancel"], "t_mcp", "get_order"),
    ("no_answer", "What is the parking fee at the clinic?", "en", None, None, None),
    ("english", "Within how many working days are approved refunds paid?", "en", ["5"], "s_pdf", None),
    ("telugu", "శాంపిల్ తీసుకునే ముందు ల్యాబ్ టెస్ట్ రద్దు చేస్తే ఎంత వాపసు వస్తుంది?", "te", ["80"], "s_pdf", None),
    ("hindi", "स्वीकृत रिफंड कितने कार्य दिवसों में दिए जाते हैं?", "hi", ["5"], "s_pdf", None),
]


def build_agent(acct: TestAccount) -> int:
    """Phase 8: the PDF, repo and docs page are added to the account's project first; the agent's source nodes
    point at them (source_ref)."""
    c = acct.client
    refs = {"s_pdf": add_file(acct, ROOT / "samples" / "refund-policy.pdf"),
            "s_repo": add_link(acct, "repo", "https://github.com/octocat/Spoon-Knife"),
            "s_url": add_link(acct, "url", "https://example.com")}
    main = ["input", "guardrails", "router", "agent", "output"]
    nodes = [{"id": f"n{i + 1}", "type": t, "config": {"model": "local", "language": "en"} if t == "agent" else {},
              "position": {"x": 300, "y": 140 * i}} for i, t in enumerate(main)]
    extra = [{"id": "s_pdf", "type": "source_doc", "config": {"source_ref": refs["s_pdf"]}},
             {"id": "s_repo", "type": "source_repo", "config": {"source_ref": refs["s_repo"]}},
             {"id": "s_url", "type": "source_doc", "config": {"source_ref": refs["s_url"]}},
             {"id": "t_calc", "type": "tool_calculator", "config": {}},
             {"id": "t_mcp", "type": "tool_mcp", "config": {"server": "orders-db", "mode": "read_only"}}]
    edges = [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(1, 5)] + [{"from": n["id"], "to": "n4"} for n in extra]
    r = c.post("/graphs", json={"project_id": acct.project_id, "name": "phase7.1 model matrix",
                                "graph": {"nodes": nodes + extra, "edges": edges}})
    r.raise_for_status()
    gid = r.json()["id"]
    by_id = {s["id"]: s["chunk_count"] for s in c.get(f"/projects/{acct.project_id}/sources").json()}
    held = {node: by_id.get(sid) for node, sid in refs.items()}
    print(f"agent #{gid}: chunks {held}", flush=True)
    return gid


def judge(test, events, model):
    tid, question, lang, facts, source, tool = test
    final = events[-1]
    if final.get("type") != "result":
        err = next((e for e in events if e.get("type") == "error"), {})
        return {"test": tid, "status": "BLOCKED" if str(err.get("code", "")).startswith("quota") else "FAIL",
                "error": err.get("message"), "code": err.get("code")}
    agent = next((e for e in events if e.get("stage") == "agent" and e.get("status") == "done"), {})
    actual = (final.get("model") or "").split(":")[0]
    fb = final.get("fallback")
    answer = final.get("answer") or ""
    cited = sorted({c["source_id"] for c in final.get("citations", [])})
    calls = [{"tool": t["tool"], "args": t["args"], "ok": t["ok"], "out": (t.get("output") or t.get("error") or "")[:80]}
             for t in final.get("tool_calls", [])]
    if facts is None:  # unanswerable: must abstain, and must not cite anything
        correct = any(d in answer for d in DONT_KNOW) and not cited
        cite_ok = not cited
    else:
        correct = any(f.lower() in answer.lower() for f in facts) and final.get("supported") is True
        cite_ok = source in cited
    tool_ok = tool is None or any(t["tool"] == tool and t["ok"] for t in calls)
    lang_ok = final.get("language_match") is not False and final.get("language") == lang
    row = {"test": tid, "requested": model, "actual": final.get("model"), "provider": PROVIDER.get(actual, actual),
           "fallback": bool(fb), "fallback_reason": f"{fb['code']}: {fb.get('reason', '')[:90]}" if fb else None,
           "ms": final.get("response_ms"), "tokens_in": (final.get("usage") or {}).get("input_tokens"),
           "tokens_out": (final.get("usage") or {}).get("output_tokens"), "cost_usd": (final.get("cost") or {}).get("usd"),
           "answer_correct": correct, "citation_correct": cite_ok, "tool_ok": tool_ok, "tools": calls,
           "language_ok": lang_ok, "retrieved": (agent.get("detail", {}).get("retrieval") or {}).get("relevant"),
           "answer": answer[:200]}
    if fb and str(fb.get("code", "")).startswith("quota"):
        row["status"] = "BLOCKED"
    elif fb or actual != model:
        row["status"] = "FAIL (fallback)"  # a fallback answer never counts for the requested model
    else:
        row["status"] = "PASS" if correct and cite_ok and tool_ok and lang_ok else "FAIL"
    return row


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--models", nargs="*", default=["local", "local_small", "groq", "gemini"])
    ap.add_argument("--graph-id", type=int, help="reuse an agent made by an earlier run")
    args = ap.parse_args()
    if args.graph_id:
        sys.exit("--graph-id is not supported since Phase 8: agents belong to a signed-in account's project; "
                 "run without it to build a fresh agent for a fresh test account.")
    acct = sign_up(args.api, "matrix")
    c = acct.client
    gid = build_agent(acct)
    rows = []
    for model in args.models:
        blocked = None
        for test in TESTS:
            if blocked:
                rows.append({"test": test[0], "requested": model, "status": "BLOCKED", "error": blocked, "requests_sent": 0})
                print(f"{model:12} {test[0]:10} BLOCKED (not sent: {blocked[:60]})", flush=True)
                continue
            body = {"question": test[1], "graph_id": gid, "model": model, "language": test[2]}
            started = time.time()
            r = c.post("/pipeline/run?stream=false", json=body)
            if r.status_code != 200:
                row = {"test": test[0], "requested": model, "status": "FAIL", "error": f"HTTP {r.status_code}: {r.text[:120]}"}
            else:
                row = judge(test, r.json()["events"], model)
            row["wall_s"] = round(time.time() - started, 1)
            rows.append(row)
            if row["status"] == "BLOCKED" and model == "gemini":
                blocked = row.get("fallback_reason") or row.get("error") or "quota"
            print(f"{model:12} {test[0]:10} {row['status']:16} actual={row.get('actual')} fb={row.get('fallback')} "
                  f"{row.get('ms')}ms tools={[t['tool'] for t in row.get('tools', [])]} :: {str(row.get('answer') or row.get('error'))[:90]}",
                  flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"graph_id": gid, "run_at": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {}
    for r in rows:
        counts[r["status"].split()[0]] = counts.get(r["status"].split()[0], 0) + 1
    print(f"\n{counts}  -> {OUT}")


if __name__ == "__main__":
    main()
