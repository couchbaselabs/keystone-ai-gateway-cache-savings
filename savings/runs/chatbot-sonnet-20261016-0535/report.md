# Keystone cache savings test

## A. Chatbot / FAQ assistant — global.anthropic.claude-sonnet-4-6

Run `chatbot-sonnet-20261016-0535`

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
| Started / finished | 2026-10-16T05:35:27.255+00:00 / 2026-10-16T05:35:51.895+00:00 |
| Dataset | Bitext customer support (27K, v11) |
| Popularity model | intent: Zipf-Mandelbrot s=1.0 q=2.0 over 27 intents; phrase: Zipf-Mandelbrot s=1.1 q=5.0 over phrasings within an intent |
| Distinct messages / groups in stream | 8,524 / 27 |
| Exact repeats / rewordings in stream | 57.4% / 42.5% |
| Requests OK / failed / retried | 50 / 0 / 0 |

| Measure | after 50 |
|---|---:|
| Requests | 50 |
| Hits (all) | 4 |
| — standard (exact) hits | 0 |
| — semantic hits | 4 |
| Hit rate | 8.0% |
| — standard hit rate | 0.0% |
| — semantic hit rate | 8.0% |
| Input tokens saved per hit | 418.0 |
| Output tokens saved per hit | 214.5 |
| Input tokens saved, total | 1,672 |
| Output tokens saved, total | 858 |
| Input / output tokens per miss | 417 / 203 |
| Embedding tokens (semantic cache), total | 520 |
| Embedding tokens per request | 10.4 |
| Latency p50 / p95, hits (ms) | 189 / 359 |
| — semantic hits (ms) | 189 / 359 |
| — standard hits (ms) | — / — |
| Latency p50 / p95, misses (ms) | 3,277 / 3,950 |
| Cost without cache (list price) | $0.22 |
| Cost with cache (LLM + embeddings) | $0.20 |
| Saved | $0.02 (8.3%) |

Hit rate within each segment:

- requests 1–50: 8.0% (semantic 8.0%)

### Wrong hits

Not judged yet (`python -m savings judge`).

### Hit rate at other thresholds (replay, after 50)

| Threshold | after 50 |
|---|---:|
| 0.70 | 16.0% |
| 0.72 | 14.0% |
| 0.74 | 14.0% |
| 0.75 | 14.0% |
| 0.76 | 12.0% |
| 0.78 | 10.0% |
| 0.80 | 10.0% |
| 0.82 | 6.0% |
| 0.84 | 6.0% |
| 0.86 | 4.0% |
| 0.88 | 2.0% |
| 0.90 | 2.0% |
| 0.92 | 2.0% |
| 0.94 | 0.0% |
| 0.96 | 0.0% |
| 0.98 | 0.0% |

Replay check at the applied threshold 0.75: replay 14.0% vs measured 8.0%.

Raw per-request data: `requests.csv` in each run directory.
