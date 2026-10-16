# Keystone cache savings test

## A. Chatbot / FAQ assistant — global.anthropic.claude-sonnet-4-6

Run `chatbot-sonnet-20261016-0537`

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
| Started / finished | 2026-10-16T05:37:39.444+00:00 / 2026-10-16T05:39:40.054+00:00 |
| Dataset | Bitext customer support (27K, v11) |
| Popularity model | intent: Zipf-Mandelbrot s=1.0 q=2.0 over 27 intents; phrase: Zipf-Mandelbrot s=1.1 q=5.0 over phrasings within an intent |
| Distinct messages / groups in stream | 8,524 / 27 |
| Exact repeats / rewordings in stream | 57.4% / 42.5% |
| Requests OK / failed / retried | 500 / 0 / 0 |

| Measure | after 500 |
|---|---:|
| Requests | 500 |
| Hits (all) | 247 |
| — standard (exact) hits | 26 |
| — semantic hits | 221 |
| Hit rate | 49.4% |
| — standard hit rate | 5.2% |
| — semantic hit rate | 44.2% |
| Input tokens saved per hit | 418.4 |
| Output tokens saved per hit | 185.1 |
| Input tokens saved, total | 103,357 |
| Output tokens saved, total | 45,709 |
| Input / output tokens per miss | 418 / 198 |
| Embedding tokens (semantic cache), total | 5,066 |
| Embedding tokens per request | 10.1 |
| Latency p50 / p95, hits (ms) | 176 / 249 |
| — semantic hits (ms) | 176 / 249 |
| — standard hits (ms) | 173 / 247 |
| Latency p50 / p95, misses (ms) | 3,174 / 4,296 |
| Cost without cache (list price) | $2.06 |
| Cost with cache (LLM + embeddings) | $1.07 |
| Saved | $1.00 (48.3%) |

Hit rate within each segment:

- requests 1–500: 49.4% (semantic 44.2%)

### Wrong hits

Not judged yet (`python -m savings judge`).

### Hit rate at other thresholds (replay, after 500)

| Threshold | after 500 |
|---|---:|
| 0.70 | 54.2% |
| 0.72 | 51.6% |
| 0.74 | 47.8% |
| 0.75 | 46.2% |
| 0.76 | 44.8% |
| 0.78 | 41.6% |
| 0.80 | 37.2% |
| 0.82 | 32.4% |
| 0.84 | 28.6% |
| 0.86 | 23.6% |
| 0.88 | 19.4% |
| 0.90 | 17.2% |
| 0.92 | 13.6% |
| 0.94 | 12.4% |
| 0.96 | 10.2% |
| 0.98 | 9.4% |

Replay check at the applied threshold 0.75: replay 46.2% vs measured 49.4%.

Raw per-request data: `requests.csv` in each run directory.
