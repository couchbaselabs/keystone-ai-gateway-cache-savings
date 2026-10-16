# Keystone cache savings test

## A. Chatbot / FAQ assistant — bedrock-global-anthropic-claude-sonnet-4-6

Run `chatbot-sonnet-20261016-0522`

| Setting | Value |
|---|---|
| Model | bedrock-global-anthropic-claude-sonnet-4-6 |
| Embedding model (cache) | amazon.titan-embed-text-v2:0 |
| Gateway | https://<gateway host not recorded> |
| Cache type | semantic |
| Similarity threshold applied | — (no debug header) |
| Expiry applied (gateway) | — |
| Expiry requested | 172800 s |
| Concurrency | 8 |
| Started / finished | 2026-10-16T05:22:32.561+00:00 / 2026-10-16T05:22:34.636+00:00 |
| Dataset | Bitext customer support (27K, v11) |
| Popularity model | intent: Zipf-Mandelbrot s=1.0 q=2.0 over 27 intents; phrase: Zipf-Mandelbrot s=1.1 q=5.0 over phrasings within an intent |
| Distinct messages / groups in stream | 8,524 / 27 |
| Exact repeats / rewordings in stream | 57.4% / 42.5% |
| Requests OK / failed / retried | 0 / 10 / 0 |

| Measure |  |
|---|
| Requests |  |
| Hits (all) |  |
| — standard (exact) hits |  |
| — semantic hits |  |
| Hit rate |  |
| — standard hit rate |  |
| — semantic hit rate |  |
| Input tokens saved per hit |  |
| Output tokens saved per hit |  |
| Input tokens saved, total |  |
| Output tokens saved, total |  |
| Input / output tokens per miss |  |
| Embedding tokens (semantic cache), total |  |
| Embedding tokens per request |  |
| Latency p50 / p95, hits (ms) |  |
| — semantic hits (ms) |  |
| — standard hits (ms) |  |
| Latency p50 / p95, misses (ms) |  |
| Cost without cache (list price) |  |
| Cost with cache (LLM + embeddings) |  |
| Saved |  |

### Wrong hits

Not judged yet (`python -m savings judge`).

Raw per-request data: `requests.csv` in each run directory.
