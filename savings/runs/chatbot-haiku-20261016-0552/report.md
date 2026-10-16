# Keystone cache savings test

## A. Chatbot / FAQ assistant — global.anthropic.claude-haiku-4-5-20251001-v1:0

Run `chatbot-haiku-20261016-0552`

| Setting | Value |
|---|---|
| Model | global.anthropic.claude-haiku-4-5-20251001-v1:0 |
| Embedding model (cache) | amazon.titan-embed-text-v2:0 |
| Gateway | https://<gateway host not recorded> |
| Cache type | semantic |
| Similarity threshold applied | 0.75 |
| Expiry applied (gateway) | 48h0m0s |
| Expiry requested | 172800 s |
| Concurrency | 8 |
| Started / finished | 2026-10-16T05:52:58.067+00:00 / 2026-10-16T05:53:30.258+00:00 |
| Dataset | Bitext customer support (27K, v11) |
| Popularity model | intent: Zipf-Mandelbrot s=1.0 q=2.0 over 27 intents; phrase: Zipf-Mandelbrot s=1.1 q=5.0 over phrasings within an intent |
| Distinct messages / groups in stream | 8,524 / 27 |
| Exact repeats / rewordings in stream | 57.4% / 42.5% |
| Requests OK / failed / retried | 100 / 0 / 0 |

| Measure | after 100 |
|---|---:|
| Requests | 100 |
| Hits (all) | 21 |
| — standard (exact) hits | 2 |
| — semantic hits | 19 |
| Hit rate | 21.0% |
| — standard hit rate | 2.0% |
| — semantic hit rate | 19.0% |
| Input tokens saved per hit | 418.7 |
| Output tokens saved per hit | 196.7 |
| Input tokens saved, total | 8,793 |
| Output tokens saved, total | 4,130 |
| Input / output tokens per miss | 418 / 184 |
| Embedding tokens (semantic cache), total | 1,040 |
| Embedding tokens per request | 10.4 |
| Latency p50 / p95, hits (ms) | 180 / 237 |
| — semantic hits (ms) | 180 / 243 |
| — standard hits (ms) | 212 / 215 |
| Latency p50 / p95, misses (ms) | 2,587 / 3,504 |
| Cost without cache (list price) | $0.14 |
| Cost with cache (LLM + embeddings) | $0.11 |
| Saved | $0.03 (21.7%) |

Hit rate within each segment:

- requests 1–100: 21.0% (semantic 19.0%)

### Wrong hits

Not judged yet (`python -m savings judge`).

### Hit rate at other thresholds (replay, after 100)

| Threshold | after 100 |
|---|---:|
| 0.70 | 31.0% |
| 0.72 | 26.0% |
| 0.74 | 25.0% |
| 0.75 | 23.0% |
| 0.76 | 21.0% |
| 0.78 | 17.0% |
| 0.80 | 15.0% |
| 0.82 | 13.0% |
| 0.84 | 12.0% |
| 0.86 | 10.0% |
| 0.88 | 8.0% |
| 0.90 | 7.0% |
| 0.92 | 5.0% |
| 0.94 | 4.0% |
| 0.96 | 3.0% |
| 0.98 | 3.0% |

Replay check at the applied threshold 0.75: replay 23.0% vs measured 21.0%.

Raw per-request data: `requests.csv` in each run directory.
