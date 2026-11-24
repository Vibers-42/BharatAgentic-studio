# Performance benchmarks (Phase 7.1)

Measured on 2026-11-24 on the development laptop, with the real stack running in Docker Desktop. Raw results:
[results/load_test.json](results/load_test.json), [results/knowledge_scaling.json](results/knowledge_scaling.json),
[results/model_matrix.json](results/model_matrix.json). Re-run with `python scripts/load_test.py`,
`docker compose exec -T api python - < scripts/knowledge_scaling.py` and `python scripts/model_matrix.py`.

**Read these as laptop numbers for a single-machine MVP, not production capacity.** Samples are small
(1-600 requests per level); p95 and p99 from fewer than about 100 requests are indicative only. Resource
figures are the peaks of `docker stats` / `nvidia-smi` samples taken every ~2 s, so short spikes can be missed.

## Hardware and environment

| | |
|---|---|
| CPU | Intel Core i7-13650HX, 14 cores / 20 threads |
| RAM | 15.7 GB (Docker Desktop VM: 7.6 GB, 20 CPUs) |
| GPU | NVIDIA RTX 3050 Laptop, 6 GB VRAM, driver 581.95, passed to the ollama container (`docker-compose.gpu.yml`) |
| Disk | 174 GB free on D: (project), 19.5 GB free on C: (Docker data) |
| Software | Windows 11, Docker 29.9.3, Postgres 16 + pgvector 0.8.7, Ollama 0.40.17, FastAPI 0.162.2 (one uvicorn worker), psycopg pool max 5 |
| Models | qwen3:14b (9.3 GB, ~43% on GPU), qwen2.5:3b (1.9 GB, fully on GPU), bge-m3 (on CPU on purpose), Groq gpt-oss-120b, Gemini 2.5 Flash |

## 1. Backend without any model (api + database)

Mixed requests: health, load agent, validate graph, list sources, usage totals, usage by model.

| Concurrency | Requests | Success | Median | P95 | P99 | Throughput | DB connections (peak) |
|---|---|---|---|---|---|---|---|
| 1 | 60 | 100% | 3.9 ms | 5.6 ms | 7.5 ms | 260 req/s | 2 |
| 5 | 60 | 100% | 8.7 ms | 19.9 ms | 24.9 ms | 506 req/s | 6 |
| 10 | 60 | 100% | 14.2 ms | 27.6 ms | 35.2 ms | 616 req/s | 6 |
| 25 | 150 | 100% | 39.4 ms | 100 ms | 123 ms | 556 req/s | 6 |
| 50 | 300 | 100% | 65.9 ms | 137 ms | 159 ms | 698 req/s | 6 |
| 100 | 600 | 100% | 152 ms | 249 ms | 349 ms | 599 req/s | 6 |

No errors up to 100 concurrent users. Throughput levels off around 600 req/s (one worker, pool of 5);
latency then grows with the queue. Database connections never exceeded the pool size.

## 2. Full pipeline with a MOCKED model

`POST /pipeline/run` with SSE, real guardrails, router, **real bge-m3 embedding of the question, real pgvector
retrieval**, claim check, usage row; only the LLM is `tests/fake_gemini.py`. This is the ceiling of
everything except text generation.

| Concurrency | Requests | Success | Median | P95 | P99 | First token (median) | Throughput |
|---|---|---|---|---|---|---|---|
| 1 | 20 | 100% | 209 ms | 218 ms | 232 ms | 65 ms | 4.8 /s |
| 5 | 20 | 100% | 502 ms | 583 ms | 671 ms | 245 ms | 9.4 /s |
| 10 | 30 | 100% | 977 ms | 1.01 s | 1.08 s | 448 ms | 9.9 /s |
| 25 | 75 | 100% | 2.49 s | 2.50 s | 2.51 s | 1.13 s | 9.9 /s |
| 50 | 150 | 100% | 4.78 s | 5.21 s | 5.25 s | 2.13 s | 10.0 /s |

Bottleneck: **bge-m3 on the CPU** (the ollama container at ~990% CPU) caps the pipeline at ~10 questions per
second; above ~5 users, requests queue. Nothing failed at 50 concurrent streams.

## 3. Real local models (Ollama, GPU + CPU)

Same RAG question on a one-PDF agent, local-only on, streamed.

| Model and load | Requests | Success | Median total | First token (median) | Throughput | GPU util / VRAM (peak) |
|---|---|---|---|---|---|---|
| qwen3:14b cold start (unloaded first) | 1 | 1/1 | 36.4 s | 30.8 s | - | 16% / 4.2 GB |
| qwen3:14b warm, one at a time | 3 | 3/3 | 5.3 s | 0.69 s | 0.18 /s | 40% / 4.2 GB |
| qwen3:14b, 2 at once | 4 | 4/4 | 9.8 s | 4.8 s | 0.21 /s | 39% / 4.2 GB |
| qwen3:14b, 3 at once | 6 | 6/6 | 15.1 s | 10.2 s | 0.20 /s | 40% / 4.2 GB |
| qwen2.5:3b cold start | 1 | 1/1 | 4.7 s | 4.2 s | - | |
| qwen2.5:3b warm, one at a time | 3 | 3/3 | 0.49 s | 0.08 s | 1.8 /s | |
| qwen2.5:3b, 2 at once | 4 | 4/4 | 0.80 s | 0.35 s | 2.2 /s | |
| qwen2.5:3b, 4 at once | 8 | 8/8 | 1.96 s | 1.51 s | 1.9 /s | 91% / 2.1 GB |

Ollama serves one request per model at a time, so concurrent users **queue**: throughput stays ~0.2 answers
per second for the 14B model however many users ask (≈ 12 answers per minute), and each extra waiting user
adds ~5 s. No failures, no timeouts, no model reloads (after the deliberate cold start the models stayed
loaded; `ollama ps` showed qwen2.5:3b 100% GPU and bge-m3 100% CPU). Tool questions make two model calls
(plan + answer): 15-30 s on the 14B model.

## 4. Real cloud models

| Model | Load | Success | Median | First token |
|---|---|---|---|---|
| Groq gpt-oss-120b | 3 one at a time | 3/3 | 0.96 s | 0.85 s |
| Groq gpt-oss-120b | 3 at once | 3/3 | 0.83 s | 0.72 s |
| Gemini 2.5 Flash | 1 | **BLOCKED** | daily free-tier quota used up (HTTP 429); fell back to qwen3:14b | |

Only 7 cloud requests were sent for this benchmark (free tiers; no paid credits).

## 5. Mixed load: uploads, questions and reads at the same moment

47 requests at once: 3 PDF uploads into one source, 4 real qwen2.5:3b questions, 40 reads (sources, agent).
All 47 succeeded. Medians: reads 200-255 ms, uploads 1.0 s, questions 6.3 s (they wait for each other and
for the embedding model). The uploaded source ended with exactly one copy (3 chunks before and after):
no duplicates, no locks left behind, no mixed contexts.

## 6. Knowledge database scaling

Synthetic 1024-dim vectors written and searched with the app's own `store.replace_source` and
`store.search`, in a temporary database (created and dropped by the script). Each size has 10 sources in
one agent plus the same number of rows in another agent with the same source ids. 30 queries per point.

| Chunks in the agent | Rows in table | Insert time | Table + indexes | Search, 1 source (median / p95) | Search, 10 sources (median / p95) | Recall@1 | Leaks |
|---|---|---|---|---|---|---|---|
| 100 | 202 | 0.3 s | 2.8 MB | 0.36 / 0.59 ms | 0.71 / 0.91 ms | 30/30 | 0 |
| 1,000 | 2,002 | 5.2 s | 29 MB | 0.96 / 1.48 ms | 4.3 / 5.0 ms | 30/30 | 0 |
| 5,000 | 10,002 | 41 s | 149 MB | 2.9 / 3.4 ms | 22 / 32 ms | 30/30 | 0 |
| 10,000 | 20,002 | 110 s | 403 MB | 4.8 / 5.3 ms | 42 / 44 ms | 30/30 | 0 |

Query plans: the agent filter uses the `(scope, source_id)` index for small sets and a sequential scan from
~5,000 chunks; the ranking is an exact sort, so recall is perfect and isolation is guaranteed by the filter.
**The HNSW index is not used by this query**, but it is maintained on every insert (most of the insert time
and about half the storage). Search stays well under 50 ms at 10,000 chunks per agent, far below model time.

Real embedding speed (bge-m3 on the CPU, 32 chunk-sized texts): **6.1 chunks per second**, so 10,000 real
chunks take ~27 minutes to ingest. Embedding, not the database, limits ingestion.

## Bottlenecks and what they mean for the collaborative MVP

| Bottleneck | Measured | Effect | Recommendation (only when needed) |
|---|---|---|---|
| Local LLM generation | 0.2 answers/s for qwen3:14b, requests queue | ~12 local answers per minute for the whole team | fine for a small team; a bigger GPU, or Groq for busy periods |
| bge-m3 on CPU | ~10 questions/s embedding ceiling, 6 chunks/s ingestion | large repositories take minutes to index, inside the request | keep for now; move ingestion to a background job if users upload large repos |
| One api worker, pool of 5 | ~600 req/s for non-LLM endpoints, 0 errors at 100 users | not a limit today | more workers only if measured |
| HNSW index unused | 50% of insert time and storage | slower uploads, bigger database | drop or replace with a filter-aware index after Phase 8 settles the query |

**Maximum stable concurrency observed:** 100 users on non-model endpoints, 50 concurrent pipeline streams
with a mocked model, 3 concurrent users on qwen3:14b and 4 on qwen2.5:3b (all 100% successful; higher loads
were not tried on the local models, as planned). Redis, Celery or Kubernetes are not justified by these
numbers.
