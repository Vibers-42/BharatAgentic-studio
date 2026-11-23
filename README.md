# BharatAgent Studio

A local, ChatGPT-style assistant where you draw an AI agent pipeline
(Input → Guardrails → Router → Agent → Output), connect your own documents, repos and tools
by drawing lines, pick any model, and ask questions in English, Telugu or Hindi. Every answer
shows its citations, the model used, and its estimated cost.

Built for **AI for Bharat**: colleges, clinics and small offices that want an assistant over
their own files, with control over privacy and spending.

## Start everything

```bash
cp .env.example .env      # then fill in your keys and a database password
docker compose up --build
```

- Web app: http://localhost:3000
- API: http://localhost:8000 (interactive docs at http://localhost:8000/docs)

Ports are bound to `127.0.0.1`, so the app is only reachable from this computer.

Pull the local models once (first run only, about 3 GB in total):

```bash
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull bge-m3
```

## Tests

```bash
pip install -r tests/requirements.txt -r api/requirements.txt
pytest tests                      # config checks; db tests skip without TEST_DATABASE_URL
bash scripts/verify_phase1.sh     # checks the running Docker stack end to end (Linux/macOS/Git Bash)
```

On Windows (PowerShell, Docker Desktop):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify_phase1.ps1
powershell -ExecutionPolicy Bypass -File scripts\verify_phase2.ps1
powershell -ExecutionPolicy Bypass -File scripts\verify_phase3.ps1                  # add -UseFakeGemini without a key
```

## Pipeline graph and API

The canvas saves the pipeline as JSON: `nodes` (`id`, `type`, `config`) and `edges` (`from`, `to`).
Examples: [default.json](api/app/pipeline/examples/default.json) (the five blocks) and
[full.json](api/app/pipeline/examples/full.json) (the handoff example with 2 sources and 2 tools).

Node types: `input`, `guardrails`, `router`, `agent`, `output`, `source_repo`, `source_doc`
(a file, or a docs link with `{"url": ...}`), `tool_calculator`, `tool_mcp`.

Validation rules (every broken rule is reported, each with its own code and message):
exactly one of each main block; the main line input → guardrails → router → agent → output has no
gaps or shortcuts; every source and tool has an edge into the agent and only into the agent;
`tool_mcp` must be `read_only` unless `allow_write: true`; agent language is `en`, `te` or `hi`.

| Endpoint | What it does |
|---|---|
| `GET /pipeline/default` | The five-block starting graph |
| `POST /pipeline/validate` | `{valid, errors[]}` for a graph |
| `POST /pipeline/run` | Runs `{question, language?, graph \| graph_id}` and streams Server-Sent Events; `?stream=false` returns JSON. An invalid graph gets 422 before anything runs |
| `POST /graphs`, `GET /graphs`, `GET /graphs/{id}` | Save (valid graphs only), list and load pipelines |

Each stage sends `running` then `done` (or `blocked` / `skipped`), then one `result` event:

```
event: stage
data: {"type": "stage", "stage": "router", "node_id": "n3", "status": "done", "ms": 0, "detail": {"route": "search", ...}}
```

While the agent writes, `token` events stream the answer text:
`{"type": "token", "stage": "agent", "node_id": "n4", "text": "..."}`.

## Answers from your PDFs (Phase 3)

1. Open http://localhost:3000, upload a PDF on the Chat tab (or `POST /sources/{source_id}/pdf`).
   It is read page by page, split into ~500-character chunks, embedded with **bge-m3** in Ollama,
   and stored in pgvector tagged with the source node id.
2. Ask a question in English, Telugu or Hindi. The agent searches **only the sources drawn into it**:
   a source without a line to the agent is never searched (the API refuses a graph with an
   unconnected source).
3. Chunks below a similarity of 0.50 are dropped. If nothing relevant is left, the agent says
   "I don't know based on the connected sources" without calling the model.
4. Gemini answers from the numbered chunks and must cite them like `[1]`. Citation numbers that
   match no chunk are removed; an answer with no valid citation is replaced by "I don't know".
5. The answer card shows each citation (file, page, chunk, similarity, snippet), the model and the
   token counts.

Sample PDFs (fictional, written for this project): [samples/](samples/) (`python samples/pdfgen.py`
regenerates them). Scanned PDFs without a text layer are rejected; OCR is future work.

Without a Gemini key, `verify_phase3.ps1 -UseFakeGemini` runs a local stand-in for the Gemini API
([tests/fake_gemini.py](tests/fake_gemini.py)) to check citations and live streaming through nginx.
With a key, the script sends 5 Gemini requests and reuses each answer for several checks.

Gemini quota (HTTP 429): a per-minute limit is retried at most twice, waiting as long as Gemini's
`RetryInfo` / `Retry-After` says (or 2 s, then 4 s), and never longer than 20 s per wait. A daily quota
is reported at once with no retry. The pipeline's error event then carries `code: "quota_rate"` or
`"quota_daily"`, and the verification script reports such checks as BLOCKED, never PASS or FAIL.

## Containers

| Container    | Job |
|--------------|-----|
| `web`        | React app (chat, canvas, usage dashboard), served by nginx; forwards `/api` to `api` |
| `api`        | FastAPI: runs the pipeline, ingests sources, wraps the models, logs usage |
| `db`         | Postgres with pgvector: graphs, embeddings, users, usage |
| `mcp-runner` | MCP tool server, on its own network with no route to `db` |
| `ollama`     | Local model and the embedding model |

Networks: `data` (api, db, ollama), `tools` (api, mcp-runner), `frontend` (web, api).

Left out on purpose to stay laptop-friendly (next steps): a mailer, and MinIO object storage
(uploaded files sit in a Docker volume instead).

## Project layout

```
docker-compose.yml   all 5 services
api/                 FastAPI backend
mcp-runner/          MCP tool server (official MCP Python SDK)
web/                 React frontend
```

## Honesty notes

- Cost is an estimate from a price table, not a bill.
- A local model has no API fee, but still uses the laptop's electricity and time.

See [PHASES.md](PHASES.md) for the build plan.
