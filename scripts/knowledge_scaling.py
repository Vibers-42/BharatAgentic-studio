"""Phase 7.1 knowledge scaling benchmark. Runs INSIDE the api container against a separate, temporary
database (bench_scale_<pid>, created and dropped by this script), so the main data is never touched:

  docker compose exec -T api python - < scripts/knowledge_scaling.py

For 100, 1,000, 5,000 and 10,000 chunks (synthetic 1024-dim unit vectors, split over 10 sources of one saved
agent, plus the same amount of noise in other agents) it measures, with the app's real store code:
insert time, table + index size, search latency for one source and for all ten (median and p95 of 30
queries), the query plan, top-1 recall of a planted chunk, and that other sources and other agents never
appear. Then it embeds 32 real chunk-sized texts with bge-m3 through Ollama to measure real embedding speed.
Prints one JSON object (the README and docs/PERFORMANCE_BENCHMARKS.md quote it).
"""

import json
import os
import statistics
import time

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from app import db
from app.config import settings
from app.rag import embeddings, store

SIZES = [100, 1000, 5000, 10000]
SOURCES = [f"bench_src{i}" for i in range(10)]
DIM = 1024
rng = np.random.default_rng(7)


def unit(n):
    v = rng.normal(size=(n, DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main():
    name = f"bench_scale_{os.getpid()}"
    admin_url = settings.database_url
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    bench_url = admin_url.rsplit("/", 1)[0] + f"/{name}"
    out = {"database": name, "sizes": []}
    try:
        with psycopg.connect(bench_url, autocommit=True) as conn:
            conn.execute((db.SCHEMA_FILE).read_text(encoding="utf-8"))
        db.pool = ConnectionPool(bench_url, min_size=1, max_size=4, open=True, configure=register_vector)
        for size in SIZES:
            t0 = time.perf_counter()
            for s in SOURCES:  # every source of agent 1 (and its namesake in agent 2) holds size/10 chunks
                total = (size // len(SOURCES))
                vecs = unit(total)
                store.replace_source(s, "source_doc", f"{s}.pdf", [(1, f"{s} chunk {i}") for i in range(total)],
                                     [list(v) for v in vecs], "graph:1")
                store.replace_source(s, "source_doc", f"{s}.pdf", [(1, f"noise {i}") for i in range(total)],
                                     [list(v) for v in unit(total)], "graph:2")  # same ids, another agent
            insert_s = time.perf_counter() - t0
            # Plant one chunk close to the query in bench_src3 of agent 1, and a closer one in agent 2.
            query = unit(1)[0]
            near = query + rng.normal(scale=0.02, size=DIM).astype(np.float32)
            store.replace_source("bench_target", "source_doc", "target.pdf", [(1, "TARGET")], [list(near / np.linalg.norm(near))], "graph:1")
            store.replace_source("bench_target", "source_doc", "target.pdf", [(1, "OTHER AGENT")], [list(query)], "graph:2")
            one, all_src, ok_recall, leaks = [], [], 0, 0
            for _ in range(30):
                t = time.perf_counter()
                found = store.search(list(query), ["bench_target", "bench_src3"], 5, "graph:1")
                one.append((time.perf_counter() - t) * 1000)
                ok_recall += found[0].content == "TARGET"
                leaks += any(c.content == "OTHER AGENT" or c.source_id not in ("bench_target", "bench_src3") for c in found)
                t = time.perf_counter()
                found = store.search(list(query), SOURCES, 5, "graph:1")
                all_src.append((time.perf_counter() - t) * 1000)
                leaks += any(c.source_id == "bench_target" or c.content.startswith("noise") for c in found)
            with db.get_conn() as conn:
                size_mb = conn.execute("SELECT pg_total_relation_size('chunks') / 1048576.0").fetchone()[0]
                vec = np.array(query, dtype=np.float32)
                plan = conn.execute(
                    """EXPLAIN (ANALYZE, FORMAT JSON) WITH connected AS MATERIALIZED (
                         SELECT id, embedding FROM chunks WHERE scope = %s AND source_id = ANY(%s))
                       SELECT id FROM connected ORDER BY embedding <=> %s LIMIT 5""",
                    ("graph:1", SOURCES, vec)).fetchone()[0][0]
                total_rows = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]

            def nodes(p):
                yield p["Node Type"] + (f" using {p['Index Name']}" if "Index Name" in p else "")
                for c in p.get("Plans", []):
                    yield from nodes(c)
            out["sizes"].append({
                "chunks_in_agent": size, "rows_in_table": total_rows, "insert_s": round(insert_s, 2),
                "table_and_indexes_mb": round(float(size_mb), 1),
                "search_one_source_ms": {"median": round(statistics.median(one), 2), "p95": round(pct(one, 95), 2), "n": 30},
                "search_ten_sources_ms": {"median": round(statistics.median(all_src), 2), "p95": round(pct(all_src, 95), 2), "n": 30},
                "recall_at_1": f"{ok_recall}/30", "isolation_leaks": leaks,
                "plan": sorted(set(nodes(plan["Plan"]))), "plan_execution_ms": round(plan["Execution Time"], 2),
            })
            print(json.dumps(out["sizes"][-1]), flush=True)
        # Real bge-m3 embedding speed (Ollama, CPU as configured), 32 chunk-sized texts in one call.
        texts = [("Patients may cancel a lab test before the sample is collected and receive a partial refund. " * 6)[:500] + f" #{i}"
                 for i in range(32)]
        t = time.perf_counter()
        vectors = embeddings.embed(texts)
        secs = time.perf_counter() - t
        out["bge_m3"] = {"texts": len(texts), "seconds": round(secs, 2), "chunks_per_second": round(len(vectors) / secs, 1),
                         "estimated_minutes_for_10000_chunks": round(10000 / (len(vectors) / secs) / 60, 1)}
    finally:
        if db.pool is not None:
            db.pool.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    print(json.dumps(out))


main()
