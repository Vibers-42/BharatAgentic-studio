# Build phases

Each phase ends with something you can run and see. Finish, test and commit a phase before
starting the next.

| # | Phase | Done when |
|---|-------|-----------|
| 1 | **Foundation**: docker-compose (5 containers, 3 networks, health checks), `.env.example`, `.gitignore`, FastAPI skeleton, Postgres schema, web status page, MCP stub | `docker compose up --build` starts all 5, and http://localhost:3000 shows every service green |
| 2 | **Pipeline engine**: graph JSON format, validator with clear errors, the five stages in their own files, guardrails, router, live status events (SSE) | Posting a graph + question streams a status event per stage; a broken graph returns a clear error |
| 3 | **RAG over a PDF**: upload, chunk, embed with bge-m3, store in pgvector, retrieve from connected sources only, Gemini answer with citations or "I don't know"; simple chat page | A cited answer from one PDF appears on screen |
| 4 | **Models and cost**: one `generate()` wrapper (Gemini, Groq, Ollama), retry then local fallback, `prices.json`, usage table, user switcher, usage endpoints, model dropdown, cost badge | Switching models changes the answer and logs a usage row |
| 5 | **More sources and languages**: GitHub repo and docs-URL connectors, Telugu/Hindi replies, cross-language retrieval, 10–15 test questions per language and an eval script per model | Telugu question finds an English doc; eval results table recorded |
| 6 | **Canvas and dashboard**: React Flow canvas with the five fixed nodes plus draggable source/tool nodes, graph JSON export, nodes light up from status events, answer card, usage dashboard with charts | Nodes light up during a question; dashboard shows per-user and per-model totals |
| 7 | **Tools and ship**: calculator tool, one read-only MCP tool in `mcp-runner`, tool calls shown on canvas and answer, README, demo script, fresh-clone test | `docker compose up` works on a clean machine; 2-minute demo runs end to end |

Never cut: cited answers, the model dropdown, cost on every answer, the Telugu and Hindi test.

## Carried-forward requirements (final delivery)

| Requirement | Where | Status |
|---|---|---|
| Document chunks are isolated between saved graphs, even when two graphs use the same source id | Phase 5 | Done: chunks are keyed by scope (`graph:<id>` or `inline`) + source id. |
| Citations must support the actual claims, not just exist | Phase 5 | Done: every cited sentence must have its numbers in the cited chunk and be close to it in meaning (bge-m3, threshold 0.45 measured on the samples); others are removed. Limit: a sentence that is on-topic and has no numbers but says something the chunk does not can still pass. |
| Canvas node highlighting is driven by the real `/pipeline/run` SSE events | Phase 6 | Done: node status comes only from the stream's stage events (by `node_id`); source highlights come from the agent's retrieval report and the result's citations. |

## Phase 5 evaluation findings (fixed)

The first runs of `scripts/eval_languages.py` found three pipeline bugs, now fixed with regression tests:
1. "Rs. 300" was split into two sentences at "Rs.", so the claim check removed correct answers.
2. Groq's gpt-oss writes citations as 【1】, which were not recognised.
3. The chunk overlap was a fixed 80 characters, so a chunk could start in the middle of a sentence
   ("test amount is refunded" without the "80 percent"); a model cited that half. The overlap is now whole
   sentences.

## Model upgrade (before Phase 6)

- Local models: `qwen3:14b` (main) with `qwen2.5:3b` as backup; Gemini and Groq unchanged. Fallback:
  chosen model -> qwen3:14b -> qwen2.5:3b.
- Local-only privacy option (request, agent node, or `LOCAL_ONLY` for the server): cloud models refused,
  fallback stays local; verified with zero requests reaching the (fake) cloud APIs.
- Optional `docker-compose.gpu.yml` gives Ollama the NVIDIA GPU; the embedding model stays on the CPU so it
  never evicts the chat model.
- Benchmark (eval/RESULTS.md): qwen3:14b 36/36, Groq gpt-oss-120b 36/36, qwen2.5:3b 21/36.

## Phase 7: tools, source status and the UI redesign

- Calculator (safe syntax-tree evaluator) and a read-only MCP server (`orders-db`: get_order, find_orders,
  order_totals) in `mcp-runner`, called with the official MCP SDK. Only tools drawn into the agent run; the
  runner refuses unconnected nodes, unlisted tools and tools without the read-only annotation; validation
  refuses unknown MCP servers.
- The chosen model plans tool calls as JSON (same on all four models); results become citable numbered
  sources; the plan and the answer are added up in the answer's tokens and cost and in one usage row.
- `tool_call` / `tool_result` events light up tool nodes; `GET /sources?graph_id=N` lets every screen show
  what a source holds, whoever uploaded it; `GET /tools`; usage filters by model and days.
- UI: sidebar shell, Builder (vertical layout, drag and drop, inspector, live event log), Chat
  (conversation, any saved agent or quick chat with tool switches), Knowledge, Usage (filters, charts),
  Status; dark and light themes; phone to desktop.
- Verified by `scripts/verify_phase7.ps1` and the browser journeys in `scripts/ui_journeys.py`.

## Phase 7.1: reliability and readiness audit

- Verified: knowledge persistence across api, web and full-stack restarts; re-use without re-upload;
  per-agent isolation under concurrent mixed requests; local-only with zero cloud requests (instrumented);
  recovery from Ollama, database, MCP and provider failures; four models on nine questions each.
- Fixed: duplicate chunks from concurrent re-ingestion (advisory lock), dead pooled connections after a
  database restart, bare 500s during database outages (now 503), no restart policies, unbounded local
  generation (`OLLAMA_NUM_PREDICT`), empty tool plans (one retry), generic error code when Ollama is down.
- Documents: docs/PHASE_7_1_AUDIT.md, docs/PERFORMANCE_BENCHMARKS.md, docs/PHASE_8_READINESS.md.
- Scripts: verify_phase7_1.ps1, model_matrix.py, load_test.py, knowledge_scaling.py, extended ui_journeys.py.

## Known issues to revisit

- `gemini-2.5-flash` is marked deprecated on Google's pricing page; from 2026-12-01 requests route to
  `gemini-3.5-flash-lite` (same listed price). Switch `GEMINI_MODEL` before the demo if needed.
- Groq's default is now `openai/gpt-oss-120b` ($0.15 / $0.60 per 1M tokens). `llama-3.3-70b-versatile` is
  not available to the current key and has no published price ("Contact Sales").
- Qwen 3 14B needs about 10 GB; on an 8 GB Docker VM it only works well with the GPU override, and its first
  answer after loading takes about a minute.
