# Serverless Model Service - Cache Savings Test Runs

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

The cache savings test requested for the Serverless Model Service: a 20,000-request stream per workload through the semantic cache, with
hit rate, tokens saved, embedding tokens, latency p50/p95 and wrong hits at 1,000, 10,000 and 20,000
requests, plus the raw per-request CSV.

```bash
npm run savings:setup        # Python venv in savings/.venv (numpy, httpx, pytest)
npm start                    # opens http://localhost:4000/savings.html in your browser
                             # (next free port if 4000 is busy; OPEN_BROWSER=false to skip)
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
**What is saved, and where.** Preferences go to `.env` and claim results to `results/claim-results.json`.
Datasets are in `savings/data/` and runs in `savings/runs/<run-id>/`: settings, progress, report,
wrong-hit verdicts, your reviews, and the raw `requests.csv` with one row per request, including the
`response` text. Job history goes to `savings/runs/jobs.json`. Everything reloads after a restart, and
the page remembers the selected run, tab and form values in the browser.

**Spot check tab.** A seeded random sample of any requests, for reading each question beside its answer.
- **Filters:** misses, standard or semantic hits, a score band (for example 0.75–0.80, just above the
  threshold), or text in the question or answer.
- **Cards:** each card shows the hit type and score, the question a hit's answer was written for,
  tokens against the expected output length, and latency.
- **Sets:** *Reload* draws a new set; *Same set* with a seed redraws an earlier one, so others can see
  the same cards.
- **Flags:** mark a card ok or wrong, with a note. Flags are saved in `spotcheck.json`. The report lists
  them in their own section and never counts them in the wrong-hit rate, because a filtered sample isn't
  random.

**Saving or sharing a run.** In a run's panel, **Download zip** offers three kinds:
- *to share*: report, `requests.csv`, the wrong-hit sample and review, settings and a README that
  explains the columns. About 4.5 MB for a 20,000-request chatbot run.
- *full run*: everything needed to restore the run. Unzip it into `savings/runs/` and the page shows it
  again; it can rebuild the report and resume. About 95 MB, most of it embedding logs.
- *full run, no embedding logs*: the same without the embedding logs, about 7 MB. The report keeps its
  threshold table, but the replay can't be recomputed and the run can't resume.

The CLI does the same: `python -m savings zip --run ID --kind share|full [--no-vectors]`.

**Run results are not committed.** `savings/runs/` is gitignored, because results feed pricing decisions
and record gateway settings. To share a run, use the page's report (open it and save the HTML) and its
CSV download, and send them privately, for example on Google Drive. The datasets are committed, except
the Bitext download and the large RAG messages file. That file rebuilds byte-for-byte, with no model
calls, from `capella-docs.jsonl` and `rag-questions.jsonl`.

`npm run savings:test` runs the unit tests and `npm run savings:smoke` runs the page's API against the
mock gateway.

## Claim checks (`/`)

A small web UI that checks, against a live gateway, how its semantic cache behaves. Each card runs real
requests and reports **CONFIRMED**, **REFUTED**, **INCONCLUSIVE** or **ERROR**, with the response headers and
usage as evidence. The cards below are the ones confirmed against the Serverless Model Service gateway.

| # | Card | Claim |
|---|---|---|
| 1 | bedrock-openai | Haiku 4.5 and Titan v2 work through the gateway; usage is `prompt_tokens`/`completion_tokens`; 1024 dims |
| 2 | hit-header | HIT/MISS is `X-Cache`; `X-cb-match-score` only with `X-cb-debug: true` |
| 3 | system-prompt-key | The system prompt is in the cache key; reordered RAG context means no HIT |
| 4 | usage-on-hit | A HIT replays the original `usage` block |
| 5 | retrieval-on-hit | A gateway HIT still pays for client-side embed + retrieval |

### Run

```bash
npm install
cp .env.example .env   # set GATEWAY_URL, GATEWAY_API_KEY and the model ids
npm start              # http://localhost:4000
```

The page opens with a **summary table** (claim number, status, result, time, a Run button per row) above
the numbered claim cards; click a row to jump to its card. The latest results survive a page reload.

**Preferences** (button at the top right, or `http://localhost:4000/#preferences`) edits everything in
`.env`: gateway URL and API key, model ids, the settle pause and the two test questions. Saving
validates the values, applies them immediately (no restart) and rewrites only those lines in `.env`.
The API key is write-only: the page never receives it; leave the field blank to keep the current value, or tick *clear*. Changing
any setting clears the previous results, since they describe the old settings.

Because the page accepts keys and URLs, the server binds to `127.0.0.1` (`HOST` in `.env`). Don't
expose it on a shared network.

#### Against a local gateway

From the `ai-gateway` repo (see its `CLAUDE.md` / `SKILLS.md`), with AWS credentials that can invoke Bedrock:

```bash
make build
REQUIRE_TENANT_ID=false LOG_LEVEL=info IS_LOCAL_CONFIG=true \
  AIGW_CONFIG_FILE=bedrock.local.config.json bin/ai-gateway
# HTTPS :8081. Create an API key with the create-api-key skill.
```

The defaults in `.env.example` match `bedrock.local.config.json`. Its Haiku model defaults to the standard
cache; the app opts each request into semantic caching with `X-cb-cache: semantic`.

#### Against a deployed gateway

Set `GATEWAY_URL`, `GATEWAY_API_KEY`, and `CHAT_MODEL` / `EMBED_MODEL` to that deployment's model ids.
Leave `INSECURE_TLS=false`.

### How runs stay clean

- Every run puts a random `[demo-session …]` nonce in the system prompt. The system prompt is part of the
  gateway's cache hash, so runs never see each other's entries or other users' entries.
- Runs are serialized on the server, so cards never overlap on the shared cache.
- The first cache card waits until the vector index answers (the gateway creates it on first use).
  `SETTLE_MS` is the pause after each store before a lookup.

### Cost

Roughly 25 Haiku calls (max 120 output tokens) and 25 Titan calls for "Run all": well under $0.05.

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
