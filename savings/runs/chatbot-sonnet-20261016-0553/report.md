# Keystone cache savings test

## A. Chatbot / FAQ assistant — global.anthropic.claude-sonnet-4-6

Run `chatbot-sonnet-20261016-0553`

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
| Started / finished | 2026-10-16T05:53:16.815+00:00 / 2026-10-16T05:53:53.093+00:00 |
| Dataset | Bitext customer support (27K, v11) |
| Popularity model | intent: Zipf-Mandelbrot s=1.0 q=2.0 over 27 intents; phrase: Zipf-Mandelbrot s=1.1 q=5.0 over phrasings within an intent |
| Distinct messages / groups in stream | 8,524 / 27 |
| Exact repeats / rewordings in stream | 57.4% / 42.5% |
| Requests OK / failed / retried | 100 / 0 / 0 |

| Measure | after 100 |
|---|---:|
| Requests | 100 |
| Hits (all) | 22 |
| — standard (exact) hits | 2 |
| — semantic hits | 20 |
| Hit rate | 22.0% |
| — standard hit rate | 2.0% |
| — semantic hit rate | 20.0% |
| Input tokens saved per hit | 418.6 |
| Output tokens saved per hit | 200.2 |
| Input tokens saved, total | 9,210 |
| Output tokens saved, total | 4,405 |
| Input / output tokens per miss | 418 / 198 |
| Embedding tokens (semantic cache), total | 1,040 |
| Embedding tokens per request | 10.4 |
| Latency p50 / p95, hits (ms) | 180 / 318 |
| — semantic hits (ms) | 180 / 322 |
| — standard hits (ms) | 177 / 195 |
| Latency p50 / p95, misses (ms) | 3,078 / 4,109 |
| Cost without cache (list price) | $0.42 |
| Cost with cache (LLM + embeddings) | $0.33 |
| Saved | $0.09 (22.1%) |

Hit rate within each segment:

- requests 1–100: 22.0% (semantic 20.0%)

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

Replay check at the applied threshold 0.75: replay 23.0% vs measured 22.0%.

Raw per-request data: `requests.csv` in each run directory.
