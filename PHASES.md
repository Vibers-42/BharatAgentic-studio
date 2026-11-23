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
| Document chunks are isolated between saved graphs, even when two graphs use the same source id | Phase 5 (source connectors rework): chunks keyed by graph scope + source id | Not started. Today chunks are keyed by source id only. |
| Citations must support the actual claims, not just exist | Phase 5 (with the Telugu/Hindi eval): check each cited sentence against its chunk | Partly done in Phase 4: an answer made only of citation markers is never "supported". Claim-level checking not started. |
| Canvas node highlighting is driven by the real `/pipeline/run` SSE events | Phase 6 | Event contract is stable: every stage event carries `node_id` and `status`. |

## Known issues to revisit

- `gemini-2.5-flash` is marked deprecated on Google's pricing page; from 2026-12-01 requests route to
  `gemini-3.5-flash-lite` (same listed price). Switch `GEMINI_MODEL` before the demo if needed.
- Groq publishes no price for `llama-3.3-70b-versatile` ("Contact Sales"), so Groq answers show no cost.
