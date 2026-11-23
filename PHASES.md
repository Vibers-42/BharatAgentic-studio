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
| Canvas node highlighting is driven by the real `/pipeline/run` SSE events | Phase 6 | Event contract is stable: every stage event carries `node_id` and `status`. |

## Phase 5 evaluation findings (fixed)

The first runs of `scripts/eval_languages.py` found three pipeline bugs, now fixed with regression tests:
1. "Rs. 300" was split into two sentences at "Rs.", so the claim check removed correct answers.
2. Groq's gpt-oss writes citations as 【1】, which were not recognised.
3. The chunk overlap was a fixed 80 characters, so a chunk could start in the middle of a sentence
   ("test amount is refunded" without the "80 percent"); a model cited that half. The overlap is now whole
   sentences.

## Known issues to revisit

- `gemini-2.5-flash` is marked deprecated on Google's pricing page; from 2026-12-01 requests route to
  `gemini-3.5-flash-lite` (same listed price). Switch `GEMINI_MODEL` before the demo if needed.
- Groq publishes no price for `llama-3.3-70b-versatile` ("Contact Sales"), so Groq answers show no cost.
