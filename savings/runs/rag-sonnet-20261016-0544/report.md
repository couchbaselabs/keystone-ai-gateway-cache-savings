# Keystone cache savings test

## B. Agentic / RAG assistant — global.anthropic.claude-sonnet-4-6

Run `rag-sonnet-20261016-0544`

| Setting | Value |
|---|---|
| Model | global.anthropic.claude-sonnet-4-6 |
| Embedding model (cache) | amazon.titan-embed-text-v2:0 |
| Gateway | https://<gateway host not recorded> |
| Cache type | semantic |
| Similarity threshold applied | 0.75 |
| Expiry applied (gateway) | 48h0m0s |
| Expiry requested | 172800 s |
| Concurrency | 8 |
| Started / finished | 2026-10-16T05:45:02.278+00:00 / 2026-10-16T05:46:35.020+00:00 |
| Dataset | Couchbase Capella docs (https://docs.couchbase.com/sitemap-cloud.xml), 315 pages, 1599 chunks |
| Popularity model | group: Zipf-Mandelbrot s=1.0 q=20.0 over 1499 base questions; variant: uniform over the base question and its 3 rewordings |
| Distinct messages / groups in stream | 4,696 / 1,473 |
| Exact repeats / rewordings in stream | 76.5% / 16.1% |
| Requests OK / failed / retried | 500 / 0 / 0 |

| Measure | after 500 |
|---|---:|
| Requests | 500 |
| Hits (all) | 425 |
| — standard (exact) hits | 10 |
| — semantic hits | 415 |
| Hit rate | 85.0% |
| — standard hit rate | 2.0% |
| — semantic hit rate | 83.0% |
| Input tokens saved per hit | 4,511.6 |
| Output tokens saved per hit | 422.9 |
| Input tokens saved, total | 1,917,420 |
| Output tokens saved, total | 179,714 |
| Input / output tokens per miss | 4,517 / 402 |
| Embedding tokens (semantic cache), total | 1,770,348 |
| Embedding tokens per request | 3,540.7 |
| Latency p50 / p95, hits (ms) | 279 / 391 |
| — semantic hits (ms) | 279 / 394 |
| — standard hits (ms) | 191 / 376 |
| Latency p50 / p95, misses (ms) | 5,897 / 7,860 |
| Cost without cache (list price) | $9.92 |
| Cost with cache (LLM + embeddings) | $1.50 |
| Saved | $8.41 (84.8%) |

Hit rate within each segment:

- requests 1–500: 85.0% (semantic 83.0%)

### Wrong hits

Not judged yet (`python -m savings judge`).

### Hit rate at other thresholds (replay, after 500)

| Threshold | after 500 |
|---|---:|
| 0.70 | 85.4% |
| 0.72 | 83.6% |
| 0.74 | 81.0% |
| 0.75 | 80.6% |
| 0.76 | 80.0% |
| 0.78 | 77.0% |
| 0.80 | 71.2% |
| 0.82 | 66.4% |
| 0.84 | 58.8% |
| 0.86 | 53.0% |
| 0.88 | 46.0% |
| 0.90 | 37.4% |
| 0.92 | 29.4% |
| 0.94 | 24.0% |
| 0.96 | 17.6% |
| 0.98 | 13.6% |

Replay check at the applied threshold 0.75: replay 80.6% vs measured 85.0%.

Raw per-request data: `requests.csv` in each run directory.
