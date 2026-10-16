# Keystone AI Gateway cache savings

Measures what the semantic cache in the Keystone AI Gateway (Couchbase Capella Model Service) saves
customers, and checks how the cache behaves against a live gateway. Two tools share one local web UI:

- **Cache savings test** (`/savings.html` plus a Python CLI). Sends a 20,000-request chatbot or RAG
  stream through the cache and reports, at 1,000, 10,000 and 20,000 requests, the hit rate (standard and
  semantic), tokens saved per hit, embedding tokens, p50/p95 latency on hits and misses, and wrong hits
  from a judged sample. It also gives the raw per-request CSV and the hit rate at other similarity
  thresholds.
- **Claim checks** (`/`). Runs live requests against the gateway to confirm or refute specific claims
  about cache behaviour.

> **Not an official Couchbase product.** This is a testing aid from Couchbase Labs, provided as is and
> without support. It runs with your own gateway URL and API key, and every test sends real, billable
> model requests.

## Cache savings test (`/savings.html`)

The Keystone cache savings test: a 20,000-request stream per workload through the semantic cache, with
hit rate, tokens saved, embedding tokens, latency p50/p95 and wrong hits at 1,000, 10,000 and 20,000
requests, plus the raw per-request CSV.

```bash
npm run savings:setup        # Python venv in savings/.venv (numpy, httpx, pytest)
npm start                    # then open http://localhost:4000/savings.html
```

On the page: build the datasets, then start a run for each workload and model. Runs are detached and
resumable, so they survive a restart of the server. When a run is done, judge 100 semantic hits and
review the verdicts, then build a summary across the three runs. Set `SONNET_MODEL` and `JUDGE_MODEL`
in Preferences. The same steps work from the shell:

```bash
cd savings
.venv/bin/python -m savings check --model haiku            # one MISS then HIT, prints the debug headers
.venv/bin/python -m savings datasets --workload chatbot    # Bitext; rag fetches Capella docs (~$2 of Haiku)
.venv/bin/python -m savings run --workload chatbot --model sonnet --run-id chatbot-sonnet
.venv/bin/python -m savings judge --run chatbot-sonnet
.venv/bin/python -m savings summary --runs chatbot-sonnet,rag-sonnet,chatbot-haiku
```

The gateway reports a match score only on a HIT and does not count embedding tokens. To fill both gaps,
the harness embeds every request through `/v1/embeddings` with the cache's model. That gives the closest
cached match for every request and the embedding token count. The report then replays the cache at
other thresholds from those vectors. It checks the replay, and the gateway's score scale, against the
measured run. `savings/savings/report.py` and the report's Method section give the details.
**What is saved, and where.** Preferences go to `.env` (not committed; it holds the API key). Claim
results go to `results/claim-results.json`. Datasets are in `savings/data/` and runs in
`savings/runs/<run-id>/`: settings, progress, report, wrong-hit verdicts and your reviews, plus the raw
`requests.csv`, which has one row per request and includes the `response` text. Job history goes to
`savings/runs/jobs.json`. Everything reloads after a restart. The page also remembers the selected run,
the tab and the form values in the browser. `savings/.gitignore` keeps the large files out of git:
`requests.csv`, `answers.jsonl`, the embedding logs and the RAG messages file. The RAG messages file
rebuilds byte-for-byte, with no model calls, from the committed `capella-docs.jsonl` and
`rag-questions.jsonl`.

`npm run savings:test` runs the unit tests and `npm run savings:smoke` runs the page's API against the
mock gateway.

## Claim checks (`/`)

A small web UI that checks, against a live AI Gateway (Model Service), the claims from the review of the
Keystone demo script (Hybrid RAG / Semantic Cache / Observe & Tune). Each card runs real requests and reports
**CONFIRMED**, **REFUTED**, **INCONCLUSIVE**, **SKIPPED** or **ERROR**, with the response headers, usage and
metric deltas as evidence.

| # | Card | Claim | Doc steps it affects |
|---|---|---|---|
| 1 | bedrock-openai | Haiku 4.5 and Titan v2 work through the gateway; usage is `prompt_tokens`/`completion_tokens`; 1024 dims | 1.3, 1.5, 2.2T, 2.9, 3.3 |
| 2 | hit-header | HIT/MISS is `X-Cache`; `X-cb-match-score` only with `X-cb-debug: true` | 2.2, 2.3, 2.6, 2.7 |
| 3 | query-vs-answer | The cache scores the new question against the stored **answer** | 2.3, 2.6, 2.8 |
| 4 | system-prompt-key | The system prompt is in the cache key; reordered RAG context means no HIT | 2.3, 2.7, 2.8 |
| 5 | usage-on-hit | A HIT replays the original `usage` block | 2.2T, 2.3T, 2.9 |
| 6 | metrics | `cache_hits`, `cached_prompt_tokens`, … replace `hit_count` / `tokens_saved_total` | 2.4, 2.7, 2.9, 3.2 |
| 7 | retrieval-on-hit | A gateway HIT still pays for client-side embed + retrieval | 2.3, 2.5, 2.8 |
| 8 | overrides | `X-cb-cache-threshold` and `X-cb-cache-expiry-duration` work per request | 2.6, 2.10 |
| 9 | false-positive | A low threshold can serve the headphones answer to another question | 2.6, 2.8 |
| 10 | cache-doc-shape | Cache docs have none of the fields the doc's SQL++ reads (needs Couchbase) | 2.1, 2.4, 2.9, 3.2 |
| 11 | per-key | Extra: whether entries are shared across API keys (needs a second key) | 2.3, 2.7 |

### Run

```bash
npm install            # add `npm install couchbase` for the cache-doc-shape card
cp .env.example .env   # set GATEWAY_URL, GATEWAY_API_KEY, model ids, METRICS_URL
npm start              # http://localhost:4000
```

The page opens with a **summary table** (claim number, status, result, time, a Run button per row) above
the numbered claim cards; click a row to jump to its card. The latest results survive a page reload.

**Preferences** (button at the top right, or `http://localhost:4000/#preferences`) edits everything in
`.env`: gateway URL and API keys, model ids, metrics URL and prefix, the settle pause, the test questions
and the optional Couchbase connection. Saving validates the values, applies them immediately (no
restart) and rewrites only those lines in `.env`. API keys and the Couchbase password are write-only:
the page never receives them; leave the field blank to keep the current value, or tick *clear*. Changing
any setting clears the previous results, since they describe the old settings.

Because the page accepts keys and URLs, the server binds to `127.0.0.1` (`HOST` in `.env`). Don't
expose it on a shared network.

#### Against a local gateway

From the `ai-gateway` repo (see its `CLAUDE.md` / `SKILLS.md`), with AWS credentials that can invoke Bedrock:

```bash
make build
REQUIRE_TENANT_ID=false LOG_LEVEL=info IS_LOCAL_CONFIG=true \
  AIGW_CONFIG_FILE=bedrock.local.config.json bin/ai-gateway
# HTTPS :8081, metrics :9090. Create an API key with the create-api-key skill.
```

The defaults in `.env.example` match `bedrock.local.config.json`. Its Haiku model defaults to the standard
cache; the app opts each request into semantic caching with `X-cb-cache: semantic`.

#### Against a deployed gateway

Set `GATEWAY_URL`, `GATEWAY_API_KEY`, `CHAT_MODEL` / `EMBED_MODEL` to that deployment's model ids, and
`METRICS_URL` if you can reach its Prometheus endpoint. Leave `INSECURE_TLS=false`.

### How runs stay clean

- Every run puts a random `[demo-session …]` nonce in the system prompt. The system prompt is part of the
  gateway's cache hash, so runs never see each other's entries or other users' entries.
- Runs are serialized on the server, because the metrics card reads shared counters. Other traffic on the
  same gateway can still move them; run that card when the gateway is idle.
- The first cache card waits until the vector index answers (the gateway creates it on first use).
  `SETTLE_MS` is the pause after each store before a lookup.

### Cost

Roughly 40 Haiku calls (max 120 output tokens) and 40 Titan calls for "Run all": well under $0.05.

### Smoke test (plumbing only)

```bash
npm run smoke
```

This starts `test/mock-gateway.js` and runs every card through the API. The mock imitates the behaviour read
from the gateway code, so a green smoke run only shows that the app works. It is **not** evidence for the
claims; only a run against a real gateway is.

## Data and licenses

- Code: Apache License 2.0 (`LICENSE`).
- `savings/data/chatbot-*`: derived from the [Bitext customer support dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset)
  (CDLA-Sharing-1.0). See `savings/data/NOTICE.md`.
- `savings/data/capella-docs.jsonl` and `rag-*`: a snapshot of the public Couchbase Capella
  documentation (docs.couchbase.com), with test questions generated from it.
- Run files record no gateway hostname: a deployed gateway is written as `<gateway host not recorded>`.
