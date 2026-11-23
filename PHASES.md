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
