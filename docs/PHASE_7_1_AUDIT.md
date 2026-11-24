# Phase 7.1 audit: reliability, persistence, privacy and readiness

Baseline: commit `2b1388c` (Phase 7), branch `main`, clean working tree, 2026-11-24. Every statement below is
backed by a check that was run on the real stack (`scripts/verify_phase7_1.ps1`, `scripts/model_matrix.py`,
`scripts/load_test.py`, `scripts/knowledge_scaling.py`, `scripts/ui_journeys.py`, `tests/`), unless it is
marked otherwise. Benchmarks: [PERFORMANCE_BENCHMARKS.md](PERFORMANCE_BENCHMARKS.md). Phase 8 proposal:
[PHASE_8_READINESS.md](PHASE_8_READINESS.md).

## 1. Actual architecture

```
browser ──> web (nginx, React build) ──/api──> api (FastAPI, 1 uvicorn worker, sync routes in a thread pool)
                                                 │  psycopg pool (1-5 connections, checked before use)
                       network "frontend"        ├──> db (Postgres 16 + pgvector 0.8.7)          network "data"
                                                 ├──> ollama (qwen3:14b, qwen2.5:3b, bge-m3; GPU) network "data"
                                                 ├──> mcp-runner (MCP SDK, orders-db, read-only)   network "tools"
                                                 └──> Groq / Gemini APIs (HTTPS, unless local-only)
```

- **Pipeline:** input → guardrails → router → agent → output, as a generator that streams SSE events per stage.
  The agent retrieves from connected sources only, plans and runs connected tools, generates with the chosen
  model (fallback: chosen → qwen3:14b → qwen2.5:3b; local-only keeps the chain local), checks citations and
  claims, and writes one usage row.
- **Ingestion** runs inside the upload request: extract → chunk → embed (bge-m3 via Ollama, CPU) → one
  transaction that replaces the source's chunks. A per-source advisory lock serialises writers (fixed in 7.1).
- **Schema** is `api/app/schema.sql`, applied on every start (`CREATE ... IF NOT EXISTS`, additive `ALTER`s).
  No migration tool.
- **Containers** have health checks, start in dependency order (`depends_on: service_healthy`), and now
  restart automatically (`restart: unless-stopped`). Ports bind to 127.0.0.1 only.

## 2. Database schema and relationships

| Table | Columns (type) | Keys and indexes |
|---|---|---|
| `users` | id serial, name text, created_at | PK id, UNIQUE name |
| `graphs` (saved agents) | id serial, name text, graph jsonb (nodes, edges, positions, node settings), updated_at | PK id |
| `chunks` | id bigserial, scope text (`graph:<id>` or `inline`), source_id text (the node id), source_type, file_name, chunk_index int, page int, content text, origin text (GitHub file or page URL), embedding vector(1024), created_at | PK id; btree (scope, source_id); btree (source_id); HNSW (embedding, cosine) |
| `usage` | id, user_id FK users, model, model_requested, status, error_code, fallback_from, input/output tokens, tokens_estimated, cost_usd, cost_inr (NULL = no published price), response_ms, local_compute_ms, sources text[], language, created_at | PK id; FK user_id; btree created_at; btree user_id |

Relationships: `usage.user_id → users.id` is the only foreign key. Chunks belong to an agent through the
`scope` string and to a node through `source_id`; there is no `sources` table (a source's state is derived
from its chunks plus the node settings in `graphs.graph`). Vector dimension 1024 (bge-m3) for all chunks.
`max_connections` 100; the api uses at most 5. Database size today ≈ 10 MB (plus 0.6 MB of uploaded PDFs).

## 3. What is stored where (persistence)

| Data | Where | Persistent? | Restart verified |
|---|---|---|---|
| Saved agents, nodes, edges, positions, node settings (model, language, local-only, links, MCP server) | `graphs.graph` (jsonb), volume `pgdata` | yes | api, web, full stack down+up |
| Extracted text, chunks, pages, file names, GitHub file paths + links, page URLs | `chunks`, `pgdata` | yes | same, chunk ids identical |
| Vector embeddings | `chunks.embedding`, `pgdata` | yes | every chunk has its vector, before and after |
| Original PDF files | volume `uploads` at `/data/uploads/graph_<id>/<source>/<file>` | yes (the file is kept, not only its text) | file present after restart |
| GitHub repository content | only as chunks (the zip is processed in memory); revision = commit in the name (`owner/repo@sha`) | yes, as a static snapshot | yes |
| Docs page content | only as chunks (title + URL kept) | yes, as a static snapshot | yes |
| Usage: model asked / answered, fallback, tokens, cost, time, sources | `usage`, `pgdata` | yes | rows still there after full restart |
| Users | `users`, `pgdata` | yes | yes |
| Local models | volume `ollama` | yes | yes |
| Conversations (chat history) | browser memory only | **no** (lost on reload) | n/a (not implemented) |
| Run events (SSE) | not stored | no (by design) | n/a |

Backup and restore: `pg_dump -Fc` restores completely (checked by restoring into a scratch database and
comparing counts; a baseline dump of 2026-11-24 is in the git-ignored `backups/`). Rollback = restore the dump
and check out the previous commit.

## 4. AI model verification (real providers, `scripts/model_matrix.py`, 2026-11-24)

One saved agent with a PDF, the Spoon-Knife GitHub repo, the example.com docs page, the Calculator and the
MCP tool; nine questions per model; the model forced per request. A fallback answer never counts as a pass
for the requested model. Results: [results/model_matrix.json](results/model_matrix.json).

| Model | Requested provider | Actual provider | RAG (PDF / repo / docs) | Calculator | MCP | No-answer abstains | Languages (en / te / hi) | Median time | Status |
|---|---|---|---|---|---|---|---|---|---|
| Qwen 3 14B | Ollama | Ollama (no fallback) | PASS / PASS / PASS | PASS (960) | PASS (get_order) | PASS | PASS / PASS / PASS | 13.2 s (cold first answer 76 s) | 9/9 |
| Qwen 2.5 3B | Ollama | Ollama (no fallback) | PASS / **FAIL** ("I don't know") / PASS | PASS (also called an unneeded find_orders) | PASS | PASS | PASS / **FAIL** (repeated the question) / PASS | 0.75 s | 7/9 |
| GPT-OSS 120B | Groq | Groq (no fallback) | PASS / PASS / PASS | PASS (after fix 6) | PASS | PASS | PASS / PASS / PASS | 1.0 s | 9/9, $0.0018 for the 9 |
| Gemini 2.5 Flash | Google | **qwen3:14b fallback** (`quota_daily`, HTTP 429) | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | - | 9 BLOCKED (1 request sent per run, then none) |

Gemini did answer one real request on 2026-11-24 in `verify_phase4.ps1` (539 tokens in, 27 out, ₹0.019,
cost matched prices.json), so the integration works; the free-tier daily quota prevents the full matrix.
The answer card and the run log show the model that actually answered (`local:qwen3:14b` with a
"Fallback from gemini (quota_daily)" badge), never the requested one.

## 5. Defects found and fixed in Phase 7.1

| # | Severity | Defect (how it was found) | Root cause | Fix (files) | Regression test | Status |
|---|---|---|---|---|---|---|
| 1 | **P1** | Six simultaneous re-uploads of one source left **120 chunks instead of 40** (integrity test) | delete-then-insert under READ COMMITTED: concurrent writers do not see each other's rows, so both inserts survive | per-source transaction advisory lock in `replace_source` and `clear_source` (`api/app/rag/store.py`) | `tests/test_integrity_db.py::test_concurrent_reingestion_*`; verify: "four uploads ... exactly one copy" | fixed |
| 2 | **P1** | After a database restart, the next requests failed ("server closed the connection") | pooled connections were handed out without a check | `check=ConnectionPool.check_connection` (`api/app/db.py`) | `test_pool_recovers_*`; verify: "database stopped ... full recovery" | fixed |
| 3 | **P1** | Database down → bare HTTP 500, after waiting up to 30 s | no handler; default pool wait 30 s | 503 "The database is not reachable right now" for `OperationalError` / `PoolTimeout`, pool wait 10 s (`api/app/main.py`, `db.py`) | `test_database_outage_is_a_clear_503_*`; verify | fixed |
| 4 | **P1** | After Docker Desktop restarted (2026-11-24), the whole stack stayed down | no `restart:` policy | `restart: unless-stopped` on all five services (`docker-compose.yml`) | verify: "every service restarts by itself" | fixed |
| 5 | **P1** | qwen2.5:3b answered one Telugu question for **675 s / 40,960 tokens** (model matrix) | no output limit on local generation; small model in a repetition loop | `num_predict` = `OLLAMA_NUM_PREDICT` (default 1536) (`api/app/models/ollama.py`, `config.py`, `.env.example`) | `tests/test_models.py::test_local_replies_have_an_output_cap`; re-run: same question 1.9 s | fixed |
| 6 | P2 | Groq returned no tool plan in 1 of 3 runs → no calculator, "I don't know" (model matrix) | GPT-OSS sometimes replies with no text content for the plan | ask once more when the plan is unreadable (`api/app/pipeline/stages/agent.py`, `api/app/tools/planner.py`) | `tests/test_tools.py::test_unreadable_tool_plan_*`, `test_tool_plan_is_retried_only_once`; re-run: Groq calculator PASS | fixed (mitigated: two empty plans in a row still skip the tool, with a warning) |
| 7 | P2 | With Ollama stopped, the run ended with the generic code `stage_error` | `EmbeddingError` had no code | code `embedding_unavailable` (`api/app/rag/embeddings.py`) | `tests/test_tools.py::test_embedding_model_down_is_a_clear_error`; verify: "Ollama stopped" | fixed |

No P0 defect was found: no data loss, no cross-agent leakage, no local-only violation, no unsafe tool
execution, no crash in normal use.

## 6. Security and privacy findings

| Area | Result | Evidence |
|---|---|---|
| SQL injection | all queries are parameterised; SQL-looking agent names are stored verbatim, a malformed model filter is 422, an unknown user 400 | verify: "SQL-looking input" |
| SSRF | docs links to 127.0.0.1, `db`, `mcp-runner`, 169.254.169.254 and `file://` are refused; redirects re-checked per hop (Phase 5 tests) | verify; `tests/test_sources_*` |
| Uploads | non-PDF content, empty files, `.exe` names (400), files over 20 MB (413), corrupt (422) and scanned PDFs (422, "no OCR") refused; previous content kept | verify |
| Path traversal | `..\..\..\evil.pdf` is stored as `evil.pdf` inside the source folder | verify; Phase 3 test |
| Calculator | syntax-tree evaluator, no `eval`; names, attributes, huge powers refused | `tests/test_tools.py` |
| Tools | only connected tools run; unknown MCP servers and write-mode MCP tools refused | Phase 7 verify + tests |
| Prompt injection | a document telling the model "every refund is 100 percent" did not change qwen3:14b's answer (80 percent, cited) | verify (one case; **not a guarantee** for other models or wordings) |
| Keys | not in `/status`, `/models`, the web bundle or the api logs | verify |
| Cross-agent leakage | 8 concurrent questions from two callers to two agents with the same source id: no mixing | verify |
| Local-only | RAG, Calculator and MCP questions, main model missing, both local models missing: **0 requests** reached the counting fake cloud (positive control: a normal request did reach it) | verify |
| **Authentication / authorization** | **missing**: the caller names the user in the request; every endpoint is open to anyone who can reach port 8000 (bound to 127.0.0.1) | by design until Phase 8 |

Local-only covers LLM calls. Adding a GitHub repo or docs link still downloads from that site, and that is
separate from local-only.

## 7. Reliability findings

| Failure | Behaviour | Status |
|---|---|---|
| Ollama stopped | clear `embedding_unavailable` / `all_providers_failed` error, at most one error row, answers again after restart | verified |
| Main local model missing | qwen2.5:3b answers, fallback shown | verified |
| Both local models missing | clear error listing only local models; no cloud call under local-only | verified |
| Groq unreachable | retried once, then the local model answers, cited | verified (connection refused; a slow Groq timeout was not simulated) |
| Gemini daily quota | exactly one request, no retry, local model answers; BLOCKED in reports | verified (fake + real) |
| MCP runner down | answer with a clear warning, no crash | Phase 7 verify |
| Database down | 503 with a clear message, automatic recovery | verified |
| Interrupted ingestion (api restarted mid-upload) | source empty or complete, never half-written | verified |
| SSE client disconnects | api stays healthy, no duplicate usage rows, next question works | verified |
| Browser refresh during a run | same as a disconnect; the answer is not resumed | verified as disconnect |
| Whole stack restart | everything comes back, nothing to re-upload | verified (script and browser) |

## 8. Architecture and scalability classification

| Area | Status |
|---|---|
| Sync routes in a thread pool (no blocking work inside `async` routes) | IMPLEMENTED AND VERIFIED |
| Database connection pooling, connection check, bounded wait | IMPLEMENTED AND VERIFIED (max 6 connections observed at 100 users) |
| Connection leaks | none observed (connections stayed ≤ 6 under all loads) |
| Request concurrency without LLM | IMPLEMENTED AND VERIFIED (100 users, 0 errors) |
| Transactions for ingestion; concurrent writers | IMPLEMENTED AND VERIFIED (after fix 1) |
| Indexes for the retrieval filter | IMPLEMENTED AND VERIFIED; the HNSW index is unused by the query (RECOMMENDED FOR FUTURE: drop or replace) |
| Vector retrieval latency | IMPLEMENTED AND VERIFIED (< 50 ms at 10,000 chunks per agent) |
| Provider retries, fallback, timeouts (Groq/Gemini 60 s, Ollama 600 s) | IMPLEMENTED; Groq/Gemini timeouts IMPLEMENTED BUT NOT VERIFIED |
| Local model output cap | IMPLEMENTED AND VERIFIED (fix 5) |
| Ollama concurrency | requests queue one at a time per model (measured); a request queue in the api is NOT REQUIRED FOR CURRENT MVP |
| Model loading / eviction | bge-m3 pinned to CPU so the chat model stays loaded (verified: no reloads under load) |
| Long-running ingestion | runs inside the request (MISSING: background jobs; RECOMMENDED FOR FUTURE if large repos are common) |
| SSE cancellation | the client side is safe; generation already in flight finishes in the background (NOT REQUIRED FOR CURRENT MVP) |
| Graceful shutdown | uvicorn default; interrupted work is rolled back (verified via interrupted ingestion) |
| Health checks, restart policies, start order | IMPLEMENTED AND VERIFIED |
| Network isolation (mcp-runner cannot reach db) | IMPLEMENTED AND VERIFIED (Phase 1, 7) |
| Resource limits per container | MISSING (Docker Desktop's VM limit applies); NOT REQUIRED FOR CURRENT MVP |
| Horizontal scaling | not possible as is: one api, one Ollama, one database (single points of failure); RECOMMENDED FOR FUTURE SCALING |
| Backups | IMPLEMENTED AND VERIFIED (manual `pg_dump`; no schedule) |
| Migrations | MISSING (start-up `IF NOT EXISTS`); needed in Phase 8 |
| Authentication / authorization / tenancy | MISSING (Phase 8) |

## 9. Strengths and weaknesses

Strengths: knowledge is durable and reusable; per-agent isolation is enforced in the query; the pipeline is
the same for four models with honest fallback reporting; local-only is enforced and provable; failures
produce clear errors and the stack recovers on its own; non-LLM paths handle 100 concurrent users on a laptop.

Weaknesses: no authentication; local generation is the throughput limit (~12 answers per minute on
qwen3:14b); ingestion speed is limited by CPU embedding (~6 chunks/s) and runs inside the request; the small
backup model is unreliable for repository and Telugu questions; there is no migration tool and no source
table; chat history is not stored.

## 10. Remaining limitations

- Gemini 2.5 Flash could not be verified with real requests on 2026-11-23/24 (free-tier daily quota, HTTP 429):
  every Gemini check is BLOCKED. Its paths were verified with the fake server (streaming, quota, fallback).
- qwen2.5:3b: answered "I don't know" for the repository question, repeated a Telugu question back instead
  of answering it (the claim check cannot catch a number-free, on-topic sentence), and sometimes calls an extra
  tool. Use it only as the backup.
- The prompt-injection check is one case on one model.
- Replaced or refused PDF files stay in the uploads folder (they are no longer indexed; clean-up is Phase 8 work).
- Saved agents cannot be deleted through the api yet.
- Groq and Gemini request timeouts were not simulated (connection refused and HTTP errors were).
- Numbers are from one laptop; they are not production capacity.
