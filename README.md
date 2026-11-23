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
powershell -ExecutionPolicy Bypass -File scripts\verify_phase4.ps1                  # fallback and failure injection
powershell -ExecutionPolicy Bypass -File scripts\verify_phase5.ps1                  # connectors, isolation, claims
powershell -ExecutionPolicy Bypass -File scripts\verify_models.ps1                  # 4 models, fallback chain, local-only
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

## Models, fallback and cost (Phase 4)

Every model sits behind one `generate()` ([api/app/models/generate.py](api/app/models/generate.py));
each provider is one small file: [gemini.py](api/app/models/gemini.py), [groq.py](api/app/models/groq.py),
[ollama.py](api/app/models/ollama.py). Four choices, all through the same retrieval, citations, claim
check, token counts, cost and usage log:

| Key | Model | Where it runs |
|---|---|---|
| `gemini` | Gemini Flash (`GEMINI_MODEL`, default `gemini-2.5-flash`) | Google API |
| `groq` | GPT-OSS 120B (`openai/gpt-oss-120b`) | Groq API |
| `local` | Qwen 3 14B (`qwen3:14b`, about 10 GB) | this computer, Ollama |
| `local_small` | Qwen 2.5 3B (`qwen2.5:3b`), the backup | this computer, Ollama |

Fallback order: the chosen model, then `local`, then `local_small`.

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
- **Cost:** [prices.json](api/app/models/prices.json) (USD per million tokens, with the date it was
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
- **Telugu and Hindi evaluation:** [eval/questions.json](eval/questions.json) has 12 questions in each
  language with the expected source; `python scripts/eval_languages.py` runs them on every model and
  writes [eval/RESULTS.md](eval/RESULTS.md).

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

### Model quality and speed, measured

From [eval/RESULTS.md](eval/RESULTS.md) (run of 2026-11-23 21:08; 12 questions per language over the same
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

See [PHASES.md](PHASES.md) for the build plan.
