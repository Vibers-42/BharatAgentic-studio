"""Phase 7.1 controlled load test against the running stack. Every part uses its own test agent and user.

  python scripts/load_test.py --part backend   # api + database only: 1, 5, 10, 25, 50, 100 concurrent users
  python scripts/load_test.py --part mocked    # full pipeline with a MOCKED LLM (tests/fake_gemini.py); the
                                               # bge-m3 embedding and the pgvector search are real
  python scripts/load_test.py --part local     # REAL Qwen 3 14B and Qwen 2.5 3B: cold, warm, 2-3 at once
  python scripts/load_test.py --part cloud     # a few REAL Groq requests (and one Gemini, quota permitting)
  python scripts/load_test.py --part mixed     # uploads, questions and reads at the same moment
  python scripts/load_test.py --part all

Records per level: requests, successes, failures, error rate, mean / median / p95 / p99 latency, requests per
second, and the peak CPU and memory of the api, db and ollama containers, database connections, and GPU use
(sampled every ~2 s). A level stops the ramp if more than 20% of its requests fail. Results are appended to
docs/results/load_test.json. Percentiles from small samples (n < 100) are indicative only.
"""

import argparse
import json
import random
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "results" / "load_test.json"
PDF = ROOT / "samples" / "refund-policy.pdf"
QUESTION = "How much is refunded if a lab test is cancelled before the sample is collected?"


def sh(*args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=ROOT).stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


class Sampler:
    """Peak resource use while a level runs: docker stats, database connections, GPU."""

    def __init__(self):
        self.peak = {}
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _keep(self, key, value):
        if value is not None:
            self.peak[key] = max(self.peak.get(key, 0), value)

    def _run(self):
        while not self._stop.is_set():
            for line in sh("docker", "stats", "--no-stream", "--format", "{{json .}}").splitlines():
                try:
                    s = json.loads(line)
                except ValueError:
                    continue
                for svc in ("api", "db", "ollama"):
                    if s.get("Name", "").endswith(f"-{svc}-1"):
                        self._keep(f"{svc}_cpu_pct", float(s["CPUPerc"].rstrip("%")))
                        mem = s["MemUsage"].split("/")[0].strip()
                        mib = float(mem[:-3]) * (1024 if mem.endswith("GiB") else 1) if mem[-3:] in ("MiB", "GiB") else None
                        self._keep(f"{svc}_mem_mib", round(mib) if mib else None)
            conns = sh("docker", "compose", "exec", "-T", "db", "sh", "-c",
                       "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -At -c \"select count(*) from pg_stat_activity where backend_type='client backend'\"")
            if conns.isdigit():
                self._keep("db_connections", int(conns))
            gpu = sh("docker", "compose", "exec", "-T", "ollama", "nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                     "--format=csv,noheader,nounits")
            if gpu and "," in gpu:
                util, mem = gpu.split(",")[:2]
                self._keep("gpu_util_pct", int(util.strip()))
                self._keep("vram_mib", int(mem.strip()))
            self._stop.wait(1.0)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=30)


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1)


def run_level(workload, concurrency, total, fn, extra=None):
    lat, errors, ttft = [], [], []
    lock = threading.Lock()

    def one(i):
        t = time.perf_counter()
        try:
            first = fn(i)
            ms = (time.perf_counter() - t) * 1000
            with lock:
                lat.append(ms)
                if first is not None:
                    ttft.append(first)
        except Exception as exc:  # noqa: BLE001 - every failure is counted and shown
            with lock:
                errors.append(f"{type(exc).__name__}: {str(exc)[:80]}")

    with Sampler() as s:
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(one, range(total)))
        duration = time.perf_counter() - start
    row = {"workload": workload, "concurrency": concurrency, "requests": total, "ok": len(lat), "failed": len(errors),
           "error_rate": round(len(errors) / total, 3), "duration_s": round(duration, 1),
           "mean_ms": round(statistics.mean(lat), 1) if lat else None, "median_ms": pct(lat, 50), "p95_ms": pct(lat, 95),
           "p99_ms": pct(lat, 99), "rps": round(len(lat) / duration, 2), "resources": s.peak,
           "errors": sorted(set(errors))[:3], **(extra or {})}
    if ttft:
        row["ttft_median_ms"] = pct(ttft, 50)
    print(json.dumps(row), flush=True)
    return row


def stream_run(client, body):
    """POST /pipeline/run and read the SSE stream to the end. Returns ms to the first token."""
    t = time.perf_counter()
    first = None
    with client.stream("POST", "/pipeline/run", json=body, timeout=900) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        last = None
        for line in r.iter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                last = ev
                if ev.get("type") == "token" and first is None:
                    first = (time.perf_counter() - t) * 1000
    if not last or last.get("type") != "result":
        raise RuntimeError(f"ended with {last.get('type') if last else 'nothing'}: {(last or {}).get('code')}")
    if last.get("fallback"):
        raise RuntimeError(f"fallback {last['fallback'].get('code')}")
    return first


def make_agent(c, name, model="local"):
    main = ["input", "guardrails", "router", "agent", "output"]
    nodes = [{"id": f"n{i + 1}", "type": t, "config": {"model": model, "language": "en"} if t == "agent" else {}}
             for i, t in enumerate(main)] + [{"id": "s_pdf", "type": "source_doc", "config": {"file": "refund-policy.pdf"}}]
    edges = [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(1, 5)] + [{"from": "s_pdf", "to": "n4"}]
    gid = c.post("/graphs", json={"name": name, "graph": {"nodes": nodes, "edges": edges}}).json()["id"]
    with open(PDF, "rb") as f:
        c.post(f"/sources/s_pdf/pdf?graph_id={gid}", files={"file": ("refund-policy.pdf", f, "application/pdf")}).raise_for_status()
    return gid, {"nodes": nodes, "edges": edges}


def compose_files():
    dev = sh("docker", "inspect", "-f", "{{json .HostConfig.DeviceRequests}}", sh("docker", "compose", "ps", "-q", "ollama"))
    gpu = dev not in ("", "null", "[]")
    return ["-f", "docker-compose.yml"] + (["-f", "docker-compose.gpu.yml"] if gpu else [])


def restart_api(env_lines, c):
    files = compose_files()
    if env_lines:
        override = ROOT / "docs" / "results" / ".load-override.yml"
        override.write_text("services:\n  api:\n    environment:\n" + "".join(f"      {l}\n" for l in env_lines), encoding="ascii")
        files += ["-f", str(override)]
    sh("docker", "compose", *files, "up", "-d", "--no-deps", "api", timeout=300)
    for _ in range(90):
        try:
            if c.get("/health", timeout=3).json().get("ok"):
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError("api did not come back")


def part_backend(c, gid, graph):
    rows = []
    calls = [lambda: c.get("/health"), lambda: c.get(f"/graphs/{gid}"), lambda: c.post("/pipeline/validate", json=graph),
             lambda: c.get(f"/sources?graph_id={gid}"), lambda: c.get("/usage/totals"), lambda: c.get("/usage/by-model?days=7")]

    def fn(i):
        r = random.choice(calls)()
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
    for conc in (1, 5, 10, 25, 50, 100):
        row = run_level("backend (no LLM): health, graph, validate, sources, usage", conc, max(60, conc * 6), fn)
        rows.append(row)
        if row["error_rate"] > 0.2:
            break
    return rows


def part_mocked(c, gid, user):
    rows = []
    fake = subprocess.Popen([sys.executable, "tests/fake_gemini.py", "8767", "0.01"], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        restart_api(["GEMINI_BASE_URL: http://host.docker.internal:8767", "GEMINI_API_KEY: fake-key"], c)
        body = {"question": QUESTION, "graph_id": gid, "model": "gemini", "user": user}
        for conc in (1, 5, 10, 25, 50):
            row = run_level("pipeline run, MOCKED LLM (real bge-m3 + pgvector + SSE)", conc, max(20, conc * 3),
                            lambda i: stream_run(c, body))
            rows.append(row)
            if row["error_rate"] > 0.2 or (row["p95_ms"] or 0) > 60000:
                break
    finally:
        fake.terminate()
        restart_api(None, c)
    return rows


def part_local(c, gid, user):
    rows = []
    for model, tag, levels in (("local", "qwen3:14b", (2, 3)), ("local_small", "qwen2.5:3b", (2, 4))):
        body = {"question": QUESTION, "graph_id": gid, "model": model, "user": user, "local_only": True}
        sh("docker", "compose", "exec", "-T", "ollama", "ollama", "stop", tag)
        time.sleep(2)
        rows.append(run_level(f"REAL {tag}: cold start (model unloaded first)", 1, 1, lambda i: stream_run(c, body)))
        rows.append(run_level(f"REAL {tag}: warm, one at a time", 1, 3, lambda i: stream_run(c, body)))
        for conc in levels:
            row = run_level(f"REAL {tag}: {conc} at the same time", conc, conc * 2, lambda i: stream_run(c, body))
            rows.append(row)
            if row["error_rate"] > 0.2:
                break
        rows[-1]["ollama_ps"] = sh("docker", "compose", "exec", "-T", "ollama", "ollama", "ps").splitlines()[1:]
    return rows


def part_cloud(c, gid, user):
    rows = []
    body = {"question": QUESTION, "graph_id": gid, "model": "groq", "user": user}
    rows.append(run_level("REAL Groq gpt-oss-120b: one at a time", 1, 3, lambda i: stream_run(c, body)))
    rows.append(run_level("REAL Groq gpt-oss-120b: 3 at the same time", 3, 3, lambda i: stream_run(c, body)))
    gem = {"question": QUESTION, "graph_id": gid, "model": "gemini", "user": user}
    rows.append(run_level("REAL Gemini 2.5 Flash: one request (quota permitting)", 1, 1, lambda i: stream_run(c, gem)))
    return rows


def part_mixed(c, gid, user):
    """Uploads, 3B questions and reads at the same moment; then checks nothing was corrupted."""
    up_gid, _ = make_agent(c, "p71 load mixed uploads")
    before = c.get(f"/sources?graph_id={up_gid}").json()["sources"][0]["chunks"]
    pdf = PDF.read_bytes()
    body = {"question": QUESTION, "graph_id": gid, "model": "local_small", "user": user, "local_only": True}
    jobs = ([("upload", lambda: c.post(f"/sources/s_pdf/pdf?graph_id={up_gid}", files={"file": ("refund-policy.pdf", pdf, "application/pdf")}))] * 3
            + [("question", lambda: stream_run(c, body))] * 4
            + [("read sources", lambda: c.get(f"/sources?graph_id={gid}"))] * 20 + [("read agent", lambda: c.get(f"/graphs/{gid}"))] * 20)
    random.shuffle(jobs)
    by_kind = {}

    def fn(i):
        kind, job = jobs[i]
        t = time.perf_counter()
        r = job()
        if hasattr(r, "status_code") and r.status_code not in (200, 201):
            raise RuntimeError(f"{kind} HTTP {r.status_code}")
        by_kind.setdefault(kind, []).append((time.perf_counter() - t) * 1000)
    row = run_level("mixed: 3 uploads + 4 REAL 3B questions + 40 reads at once", len(jobs), len(jobs), fn)
    after = c.get(f"/sources?graph_id={up_gid}").json()["sources"][0]["chunks"]
    row["by_kind_median_ms"] = {k: pct(v, 50) for k, v in by_kind.items()}
    row["chunks_before_after"] = [before, after]
    row["consistent"] = before == after
    print(json.dumps({"mixed_consistent": row["consistent"], "chunks": [before, after]}), flush=True)
    return [row]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--part", default="all", choices=["backend", "mocked", "local", "cloud", "mixed", "all"])
    args = ap.parse_args()
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=200)
    c = httpx.Client(base_url=args.api, timeout=900, limits=limits)
    stamp = time.strftime("%m%d%H%M%S")
    user = f"p71load_{stamp}"
    c.post("/users", json={"name": user})
    gid, graph = make_agent(c, f"p71 load {stamp}")
    parts = ["backend", "mocked", "local", "cloud", "mixed"] if args.part == "all" else [args.part]
    results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"runs": []}
    for p in parts:
        rows = globals()[f"part_{p}"](c, gid, graph if p == "backend" else user)
        results["runs"].append({"part": p, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": gid, "rows": rows})
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
