# BharatAgent Studio: technical details

Everything about running, testing and how each part of the prototype works, phase by phase. The short
overview (main features, and what is left for the final product) is in the [main README](../README.md).

## Start everything

Windows with Docker Desktop (PowerShell):

```powershell
Copy-Item .env.example .env     # then fill in your keys and a database password
docker compose up -d --build
```

With an NVIDIA GPU, start with the GPU override instead, so Qwen 3 14B runs partly on the GPU:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

- Web app: http://localhost:3000
- API: http://localhost:8000 (interactive docs at http://localhost:8000/docs)

Ports are bound to `127.0.0.1`, so the app is only reachable from this computer.

Pull the local models once (first run only, about 13 GB in total):

```powershell
docker compose exec ollama ollama pull qwen3:14b
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull bge-m3
```

Gemini and Groq need `GEMINI_API_KEY` and `GROQ_API_KEY` in `.env`; without them the app still works
with the two local models (and the Status page says which models are ready).

Open http://localhost:3000 and choose **Get started** to create the first account; it gets a personal
workspace with a first project. Data from before Phase 8 is kept in a hidden "Legacy workspace (imported)"
until an operator gives it to an account:

```powershell
docker compose exec api python -m app.admin claim-legacy you@example.com
```

## Tests

```bash
pip install -r tests/requirements.txt -r api/requirements.txt
pytest tests                      # unit tests; db tests skip without TEST_DATABASE_URL
cd web && npm test                # canvas model, SSE highlighting, formatting (Vitest)
python scripts/ui_journeys.py     # four people in four browser profiles (owner, contributor, consumer, outsider)
python scripts/verify_phase8_sources.py  # all 8 source types shared by two employees, real Groq + Qwen 3 14B
python scripts/model_matrix.py    # the same 9 questions on all 4 models, real providers (docs/results)
python scripts/load_test.py       # controlled load test (docs/PERFORMANCE_BENCHMARKS.md)
bash scripts/verify_phase1.sh     # checks the running Docker stack end to end (Linux/macOS/Git Bash)
```

On Windows (PowerShell, Docker Desktop):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify_phase1.ps1
powershell -ExecutionPolicy Bypass -File scripts\verify_phase2.ps1
powershell -ExecutionPolicy Bypass -File scripts\verify_phase3.ps1                  # add -UseFakeGemini without a key
powershell -ExecutionPolicy Bypass -File scripts\verify_phase4.ps1                  # fallback and failure injection
powershell -ExecutionPolicy Bypass -File scripts\verify_phase5.ps1                  # connectors, isolation, claims
powershell -ExecutionPolicy Bypass -File scripts\verify_models.ps1                  # 4 models, fallback chain, local-only
powershell -ExecutionPolicy Bypass -File scripts\verify_phase6.ps1                  # builder, validation, SSE per node, dashboard
powershell -ExecutionPolicy Bypass -File scripts\verify_phase7.ps1                  # tools, MCP security, source status, UI journeys
powershell -ExecutionPolicy Bypass -File scripts\verify_phase7_1.ps1                # persistence, integrity, privacy, recovery, security
powershell -ExecutionPolicy Bypass -File scripts\verify_phase8.ps1                  # accounts, workspaces, invitations, isolation, shared knowledge
powershell -ExecutionPolicy Bypass -File scripts\verify_all.ps1                     # all of the above in order, with a summary
```

Since Phase 8 every script signs in as a fresh test account through the real `/auth/register` (its own
session cookie and CSRF token, a random password that is never printed) and works in that account's own
project; there is no test-only way around authentication.

A run is only valid while Docker Desktop and the stack are up: the scripts report SKIP (never PASS) for checks
they could not reach, and `verify_phase7_1.ps1` exits with code 2 if the api was not reachable at all.

## Pipeline graph and API

The canvas saves the pipeline as JSON: `nodes` (`id`, `type`, `config`) and `edges` (`from`, `to`).
Examples: [default.json](../api/app/pipeline/examples/default.json) (the five blocks) and
[full.json](../api/app/pipeline/examples/full.json) (the handoff example with 2 sources and 2 tools).

Node types: `input`, `guardrails`, `router`, `agent`, `output`, `source_repo`, `source_doc`
(a file, or a docs link with `{"url": ...}`), `tool_calculator`, `tool_mcp`.

Validation rules (every broken rule is reported, each with its own code and message):
exactly one of each main block; the main line input → guardrails → router → agent → output has no
gaps or shortcuts; every source and tool has an edge into the agent and only into the agent;
`tool_mcp` must be `read_only` unless `allow_write: true`, and its server must be one the api knows
(`orders-db`); agent language is `en`, `te` or `hi`; agent model is one of the four keys.

| Endpoint | What it does |
|---|---|
| `GET /pipeline/default` | The five-block starting graph |
| `POST /pipeline/validate` | `{valid, errors[]}` for a graph |
| `POST /pipeline/run` | Runs `{question, language?, graph \| graph_id}` and streams Server-Sent Events; `?stream=false` returns JSON. An invalid graph gets 422 before anything runs |
| `POST /graphs`, `PUT /graphs/{id}`, `GET /graphs`, `GET /graphs/{id}` | Save (valid graphs only), update, list and load pipelines |
| `GET /sources?graph_id=N` | What every source of a saved agent holds (chunks, files, when indexed); without `graph_id`, the quick chat's sources |
| `POST /sources/{id}/pdf`, `/url`, `/repo`, `DELETE /sources/{id}` | Add content to a source node (add `?graph_id=N` for a saved agent), or clear it |
| `GET /tools` | The calculator and the read-only tools each MCP server offers (listed live from the mcp-runner) |
| `GET /models`, `GET /status` | Models with readiness and prices; container health |
| `GET /usage/totals`, `/by-user`, `/by-model`, `/by-day`, `/recent` | Usage; filters `?user=`, `?model=` (key), `?days=` |

Each stage sends `running` then `done` (or `blocked` / `skipped`), then one `result` event:

```
event: stage
data: {"type": "stage", "stage": "router", "node_id": "n3", "status": "done", "ms": 0, "detail": {"route": "search", ...}}
```

While the agent writes, `token` events stream the answer text:
`{"type": "token", "stage": "agent", "node_id": "n4", "text": "..."}`. When it uses a tool, a
`tool_call` event (`node_id`, `tool`, `args`) and then a `tool_result` event (`ok`, `output` or `error`,
`ms`) are sent for the tool's node, so the canvas lights up that node.

## Answers from your PDFs (Phase 3)

1. Open http://localhost:3000, upload a PDF on the Knowledge page (or `POST /sources/{source_id}/pdf`).
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

Sample PDFs (fictional, written for this project): [samples/](../samples/) (`python samples/pdfgen.py`
regenerates them). Scanned PDFs without a text layer are rejected; OCR is future work.

Without a Gemini key, `verify_phase3.ps1 -UseFakeGemini` runs a local stand-in for the Gemini API
([tests/fake_gemini.py](../tests/fake_gemini.py)) to check citations and live streaming through nginx.
With a key, the script sends 5 Gemini requests and reuses each answer for several checks.

Gemini quota (HTTP 429): a per-minute limit is retried at most twice, waiting as long as Gemini's
`RetryInfo` / `Retry-After` says (or 2 s, then 4 s), and never longer than 20 s per wait. A daily quota
is reported at once with no retry. The pipeline's error event then carries `code: "quota_rate"` or
`"quota_daily"`, and the verification script reports such checks as BLOCKED, never PASS or FAIL.

## Models, fallback and cost (Phase 4)

Every model sits behind one `generate()` ([api/app/models/generate.py](../api/app/models/generate.py));
each provider is one small file: [gemini.py](../api/app/models/gemini.py), [groq.py](../api/app/models/groq.py),
[ollama.py](../api/app/models/ollama.py). Four choices, all through the same retrieval, citations, claim
check, token counts, cost and usage log:

| Key | Model | Where it runs |
|---|---|---|
| `gemini` | Gemini Flash (`GEMINI_MODEL`, default `gemini-2.5-flash`) | Google API |
| `groq` | GPT-OSS 120B (`openai/gpt-oss-120b`) | Groq API |
| `local` | Qwen 3 14B (`qwen3:14b`, about 10 GB) | this computer, Ollama |
| `local_small` | Qwen 2.5 3B (`qwen2.5:3b`), the backup | this computer, Ollama |

Fallback order: the chosen model, then the other cloud model (Groq ↔ Gemini), then `local`, then `local_small`; a local model falls back to the other local one and then to the cloud models, unless Local only is on.

**Running Qwen 3 14B:** pull it once (`docker compose exec ollama ollama pull qwen3:14b`). With an NVIDIA
GPU, start with `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d` so part of the
model sits in video memory (on a 6 GB RTX 3050: about 43% GPU, 57% CPU; ~5-10 s per answer once loaded,
about 60-75 s for the first answer while the model loads). Without a GPU it runs on the CPU, much slower.
The embedding model is kept on the CPU so it never pushes the chat model out of GPU memory.

**Local-only privacy option:** tick "Local only" in the chat (or send `"local_only": true`, or set
`"local_only": true` on the agent node, or `LOCAL_ONLY=true` in `.env` for everyone). Then a cloud model
is refused with an error before anything runs, and the fallback chain stays local, so no question or
document text is sent to any LLM API. Fetching a docs link or GitHub repo you add is still a download
from that site.

- **Choose a model** per question in the chat dropdown (or `"model"` in `/pipeline/run`); the agent
  node's `model` setting is the default. `GET /models` lists the models, whether each is ready, and prices.
- **Retry and fallback:** a passing failure (per-minute limit, network, server error) is retried once,
  waiting as the provider asks (at most 20 s); then the local model answers instead. A daily quota or
  a missing key goes straight to the local model. If the local model fails too, the answer is one
  `all_providers_failed` error listing every attempt. A safety refusal is never sent to another model.
  The stream shows a `fallback` event, and `token_reset` if a model failed mid-answer.
- **Tokens:** actual counts from each provider; if a provider sends none, they are estimated with the
  tiktoken tokenizer and marked `"estimated": true`.
- **Cost:** [prices.json](../api/app/models/prices.json) (USD per million tokens, with the date it was
  checked), shown in INR using `USD_TO_INR`. A model with no published price shows no cost rather than
  zero. The local model has no API fee; its compute time is shown separately. Every answer also shows
  what the same tokens would cost on the other models. Costs are estimates, not a bill.
- **Usage:** one row per model answer (including fallbacks and failures) in Postgres. Pick the user in
  the header. Endpoints: `GET /usage/totals`, `/usage/by-user`, `/usage/by-model`, `/usage/by-day`,
  `/usage/recent` (add `?user=<name>`); `GET /users`, `POST /users`.

## More sources, isolation and claim checks (Phase 5)

Three connectors, all chunked, embedded with bge-m3 and stored per source node:

| Source | How to add it | Notes |
|---|---|---|
| PDF | Chat page, or `POST /sources/{id}/pdf` | text per page; scanned PDFs are refused |
| Docs link | Chat page, or `POST /sources/{id}/url {"url": ...}` | one page (HTML, text or a PDF link); links are not followed |
| GitHub repo | Chat page, or `POST /sources/{id}/repo {"url": "https://github.com/owner/repo"}` | one zip download; docs and code files, per file, each chunk linked to the file at that commit; `GITHUB_TOKEN` for private repos |

- **Links are fetched safely:** only http(s), every redirect checked, and any address that is private or
  internal (Docker services such as `ollama` and `db`, `127.0.0.1`, `169.254.169.254`) is refused.
- **Saved graphs keep their documents apart.** Add `?graph_id=N` to a source endpoint to store chunks for
  that saved graph only; running the graph by `graph_id` searches only them, even if another graph uses
  the same source id. Graphs sent with the question use their own "inline" scope.
  `POST /graphs/{id}/ingest` ingests every link-based source of a saved graph from its node settings.
- **Citations must back the claims.** Each sentence of an answer is checked against the chunk it cites:
  every number in it must appear in that chunk, and its meaning must be close to it (bge-m3, works across
  languages). Unsupported or uncited sentences are removed and listed on the answer card; if nothing is
  left, the answer is "I don't know".
- **Telugu and Hindi evaluation:** [eval/questions.json](../eval/questions.json) has 12 questions in each
  language with the expected source; `python scripts/eval_languages.py` runs them on every model and
  writes [eval/RESULTS.md](../eval/RESULTS.md).

## Visual agent builder (Phase 6, redesigned in Phase 7)

Open http://localhost:3000. The **Builder** is a React Flow canvas:

- The five fixed blocks run top to bottom: Input, Guardrails, Router, Agent, Output. They cannot be deleted.
- **Drag** a PDF, Docs link, GitHub repo, Calculator or MCP tool from the palette onto the canvas (or click
  it), then **draw a line** from its handle to the Agent: sources go into the Agent's left side, tools into
  its right side. The inspector's "Connected to Agent" switch does the same from the keyboard. A source or
  tool without a line is never used.
- Click a block to configure it in the **Inspector**: the Agent's model (Gemini, Groq GPT-OSS 120B,
  Qwen 3 14B, Qwen 2.5 3B), answer language and **Local only**; Guardrails checks; a source's PDF upload
  (drop zone with upload progress), docs link or GitHub repo; an MCP tool's server, mode and the tools
  the server really offers.
- Each source block shows what it holds (chunks and file), read from the api, so an upload made through
  the Knowledge page, another tab or the API shows up too, after saving and after reloading.
- **Save / Open / New / Tidy / Validate**: saved agents keep their layout; graphs saved with the older
  left-to-right layout are tidied when opened. Documents belong to a saved agent, so save once before
  adding a source's content. Validation problems are listed and marked on their block.
- **Run** streams the real pipeline from the api's events: each block shows Running, Done, Blocked or
  Failed; sources turn Searched and then Cited; tools show Running, then their result (Called) and Cited;
  the lines into the running block move and cited lines glow. The Run tab lists every event with its time,
  then the answer with citations, tool calls, model, tokens, cost and time.

## Tools: calculator and MCP (Phase 7)

Tools only run when they are drawn into the Agent, and only when the router decides the question needs one
(a calculation with a calculator connected, or an order question with an MCP tool connected).

1. **Plan:** the same model the user chose is asked for a small JSON plan (`{"calls": [{"node_id", "tool",
   "args"}]}`) listing only the connected tools. This works the same way on all four models. A call to a
   tool that was not offered is dropped and reported.
2. **Run:** [runner.py](../api/app/tools/runner.py) runs each call, and refuses a node that is not connected,
   an MCP tool the server did not list, and an MCP tool that is not marked read-only (unless the node has
   `allow_write: true`). A failing tool is reported as a result (`ok: false`), never a crash.
3. **Answer:** each result becomes a numbered source the model must cite, next to the document chunks.
   The claim check verifies the numbers in a sentence against the tool's output.
4. **Account:** the plan and the answer are both model calls; their tokens, cost and time are added up
   into the answer and into one usage row.

- **Calculator** ([calculator.py](../api/app/tools/calculator.py)): a safe evaluator over Python's syntax
  tree (no `eval`): `+ - * / // % **`, `round abs min max sqrt`, and everyday forms such as `1,200 x 80%`,
  `18% of 4500` and `Rs. 300`. Names, attributes, strings, huge powers and division by zero are refused.
- **MCP tool** (`orders-db`, in the `mcp-runner` container, official MCP Python SDK): `get_order`,
  `find_orders` and `order_totals` over fictional clinic orders ([orders.json](../mcp-runner/orders.json)),
  annotated read-only, and `cancel_order`, the one tool that changes data: it is offered only when the MCP
  block has "Allow actions that change data" on, and runs only after the user clicks Approve (see What's new).
  The api talks to it with the SDK's streamable HTTP client and can reach only the servers it knows; the
  mcp-runner has no network path to the database. Cancellations live in memory and reset on restart.

Example (Qwen 3 14B, local only): "My lab test cost Rs. 1200 and I cancelled before the sample was
collected. How much will I get back?" plans `calculator("1200 * 80%")`, gets `960`, and answers
"You will get back 80 percent of the test amount, which is Rs. 960 [1][4]", citing the refund PDF and the
calculator. "What is the status of order ORD-1002?" calls `get_order` and cites `orders-db.get_order`.

## Accounts, workspaces and shared projects (Phase 8)

Plan, implementation, security and demo: [docs/PHASE_8_PLAN.md](PHASE_8_PLAN.md),
[docs/PHASE_8_IMPLEMENTATION.md](PHASE_8_IMPLEMENTATION.md), [docs/PHASE_8_SECURITY.md](PHASE_8_SECURITY.md),
[docs/PHASE_8_DEMO.md](PHASE_8_DEMO.md).

- **Accounts:** registration and sign-in with an email and a password (Argon2id). The session is an
  HttpOnly cookie (`SameSite=Lax`; set `COOKIE_SECURE=true` behind HTTPS) that the server stores only as a
  hash; every change also needs the CSRF token from `/auth/me`. Repeated wrong passwords are slowed down.
- **Workspaces:** everyone has a personal workspace; anyone can create a **company** workspace and becomes
  its owner. Roles: **owner** (everything, including roles), **admin** (members, invitations, projects),
  **member** (works in the projects they are given).
- **Invitations:** an owner or admin creates a single-use link for one email address (no email is sent:
  share the link yourself). The invited person signs in or registers with that email and accepts; an owner
  or admin then **confirms** the membership.
- **Projects:** each has its own members (**contributor** adds knowledge and agents, **viewer** reads and
  asks), knowledge, agents and conversations. A project can be **local only**: no cloud model is ever used.
- **Knowledge** is added once per project (PDF, Word, Markdown, TXT, CSV, Excel, a GitHub repo or a docs
  page) and used by every agent of the project; agents point at sources instead of holding copies.
- **Conversations** are private to the person who started them, stored with every answer, its citations,
  model, tokens and cost, and follow-up questions see the recent turns.
- **Usage** belongs to the person, workspace, project and agent of each request; members see their own,
  owners and admins see the whole workspace.
- **Isolation:** every request is checked on the server against the signed-in account (never a user id or
  role sent by the browser); another workspace's or project's ids give 404, and removing someone takes effect
  on their next request without signing them out.

## Chat, Knowledge, Usage and Status

- **Chat:** ask the project's knowledge (all its ready sources) or one of its shared agents. Choose the
  model and the language, switch **Local only** on, and ask. Each answer shows the five stages, tool calls,
  the answer with clickable citation numbers, the model, tokens, cost and time. Conversations are listed on
  the left and stay after sign-out and restarts.
- **Knowledge:** the project's shared sources: upload files (drag and drop, with progress), fetch docs
  links, download GitHub repos; see status, chunks, who added each and when; re-index, replace, download or
  delete (asks first; cannot be undone). Viewers only read.
- **Usage:** requests, tokens, estimated cost and answer time for you or, for owners and admins, the whole
  workspace; by person, model, project and day (India time, with a table view), and the latest answers.
- **Status:** health of the containers, which models are ready (and how to set up the others), prices,
  and the tools the MCP runner offers.

The interface is dark by default with a light theme (switch at the bottom of the sidebar), works from
phone to desktop width (the sidebar becomes a menu on small screens), uses no web fonts or icon packages,
and has visible keyboard focus and labelled controls.

## Reliability, backups and limits (Phase 7.1)

Audit, benchmarks and the Phase 8 plan: [docs/PHASE_7_1_AUDIT.md](PHASE_7_1_AUDIT.md),
[docs/PERFORMANCE_BENCHMARKS.md](PERFORMANCE_BENCHMARKS.md), [docs/PHASE_8_READINESS.md](PHASE_8_READINESS.md).

- **Everything is persistent:** agents, settings, chunk text and vectors in Postgres (`pgdata` volume); the
  original uploaded PDFs in the `uploads` volume; models in the `ollama` volume. Restarting the api, the web,
  or the whole stack (`docker compose down` + `up`, without `-v`) loses nothing, and nothing has to be
  uploaded again.
- **Automatic restart:** every service has `restart: unless-stopped`, so the stack comes back after Docker
  Desktop or the computer restarts.
- **Back up and restore** (keep the file somewhere safe; `backups/` is git-ignored):

  ```powershell
  docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/backup.dump'
  docker compose cp db:/tmp/backup.dump backups/backup.dump
  ```

  To restore into an empty database: `docker compose cp backups/backup.dump db:/tmp/backup.dump`, then
  `docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/backup.dump'`.
  Never run `docker compose down -v` on the main stack: `-v` deletes the volumes.
- **Database outages** give a clear `503 The database is not reachable right now` and the api reconnects by
  itself when the database is back.
- **Local output cap:** a local model writes at most `OLLAMA_NUM_PREDICT` tokens per reply (default 1536);
  without it the 3B model once looped for 11 minutes.

## Containers

| Container    | Job |
|--------------|-----|
| `web`        | React app (chat, canvas, usage dashboard), served by nginx; forwards `/api` to `api` |
| `api`        | FastAPI: runs the pipeline, ingests sources, wraps the models, logs usage |
| `db`         | Postgres with pgvector: accounts, sessions, workspaces, projects, sources, embeddings, agents, conversations, usage |
| `mcp-runner` | MCP tool server (`orders-db`, read-only tools), on its own network with no route to `db` |
| `ollama`     | Local model and the embedding model |

Networks: `data` (api, db, ollama), `tools` (api, mcp-runner), `frontend` (web, api).

Left out on purpose to stay laptop-friendly (next steps): a mailer (invitation links are shared by hand),
and MinIO object storage (uploaded files sit in a Docker volume instead).

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
- A question that uses a tool costs two model calls (the tool plan and the answer). On Qwen 3 14B on this
  laptop the plan takes about 6-10 s once the model is loaded (about 20-35 s for the first one), so a tool
  answer takes 15-30 s locally; on Groq it takes 1-3 s.
- Accounts and roles are checked on every request since Phase 8, but this is still a laptop deployment:
  the ports are bound to `127.0.0.1`, there is no HTTPS, no email verification and no password reset by
  email (an invitation is confirmed by an owner or admin instead), and the sign-in rate limit is kept in the
  api's memory (it resets when the api restarts). Put it behind HTTPS with `COOKIE_SECURE=true` before
  exposing it to a network.
- Tools are chosen by simple, explainable router rules (a calculation, or a question about orders); a
  question phrased differently may not use a tool. The orders data is fictional.

### Model quality and speed, measured

From [eval/RESULTS.md](../eval/RESULTS.md) (run of 2026-11-23 21:08; 12 questions per language over the same
sample PDFs, same pipeline, citations and claim check for every model). Times are the median per answer,
measured on this laptop (RTX 3050 6 GB, 8 GB for Docker), with the local model already loaded:

| Model | English | Telugu | Hindi | Median time (en / te / hi) |
|---|---|---|---|---|
| Groq `openai/gpt-oss-120b` | 12/12 | 12/12 | 12/12 | 0.7 / 1.0 / 0.8 s |
| Local `qwen3:14b` (GPU + CPU) | **12/12** | **12/12** | **12/12** | 4.6 / 13.8 / 10.2 s |
| Local backup `qwen2.5:3b` | 11/12 | 3/12 | 7/12 | 0.6 / 0.9 / 0.8 s |
| Gemini `gemini-2.5-flash` | 1/1 answered | not measured | not measured | daily free quota used up after 1 request |

Qwen 3 14B matches Groq on every question in all three languages. Its costs are time and memory: the
first answer after it is loaded takes about 50-75 s (it is about 10 GB and has to be read into the GPU and
RAM), Telugu answers take longer because Telugu needs many more tokens, and it needs the GPU override
(`docker-compose.gpu.yml`) to be practical on a laptop.

What goes wrong with the small backup model (qwen2.5:3b), in its own answers:
- **Answers in English** to Telugu or Hindi questions (3 of 12 each in the last run). The app flags these
  on the answer ("not written in Telugu") instead of hiding them.
- **Wrong numbers or a different fact** in Telugu and Hindi: "3 days" instead of 7, or the gate time when
  asked about visiting hours. The claim check removes a number that is not in the cited source, but it
  cannot tell that a true fact answers the wrong question.
- **Answers questions that have no answer**: asked for the yearly hostel rent, it gave the monthly mess
  fee (a true, cited fact, but not what was asked).
- **Gives up** ("I don't know") on some answerable questions, or writes sentences without citations,
  which the claim check then removes.

So: Qwen 3 14B (or Groq/Gemini) for real use, including Telugu and Hindi; the 3B model only as the
always-available backup. The answer badge warns about the 3B model in Telugu and Hindi.
The Telugu and Hindi test questions were written for this project and should be reviewed by a native speaker.

See [PHASES.md](../PHASES.md) for the build plan.
