# Keystone cache savings test

## A. Chatbot / FAQ assistant — global.anthropic.claude-haiku-4-5-20251001-v1:0

Run `chatbot-haiku-20261016-0608`

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
| Started / finished | 2026-10-16T06:08:48.778+00:00 / 2026-10-16T06:39:09.955+00:00 |
| Dataset | Bitext customer support (27K, v11) |
| Popularity model | intent: Zipf-Mandelbrot s=1.0 q=2.0 over 27 intents; phrase: Zipf-Mandelbrot s=1.1 q=5.0 over phrasings within an intent |
| Distinct messages / groups in stream | 8,524 / 27 |
| Exact repeats / rewordings in stream | 57.4% / 42.5% |
| Requests OK / failed / retried | 20,000 / 0 / 0 |

| Measure | after 1,000 | after 10,000 | after 20,000 |
|---|---:|---:|---:|
| Requests | 1,000 | 10,000 | 20,000 |
| Hits (all) | 604 | 8,854 | 18,577 |
| — standard (exact) hits | 68 | 1,431 | 3,184 |
| — semantic hits | 536 | 7,423 | 15,393 |
| Hit rate | 60.4% | 88.5% | 92.9% |
| — standard hit rate | 6.8% | 14.3% | 15.9% |
| — semantic hit rate | 53.6% | 74.2% | 77.0% |
| Input tokens saved per hit | 417.3 | 417.2 | 417.1 |
| Output tokens saved per hit | 183.3 | 184.5 | 185.0 |
| Input tokens saved, total | 252,051 | 3,693,657 | 7,749,018 |
| Output tokens saved, total | 110,691 | 1,633,617 | 3,436,412 |
| Input / output tokens per miss | 417 / 186 | 417 / 185 | 416 / 185 |
| Embedding tokens (semantic cache), total | 9,937 | 91,822 | 180,439 |
| Embedding tokens per request | 9.9 | 9.2 | 9.0 |
| Latency p50 / p95, hits (ms) | 177 / 264 | 189 / 354 | 187 / 353 |
| — semantic hits (ms) | 177 / 264 | 190 / 351 | 188 / 352 |
| — standard hits (ms) | 177 / 279 | 184 / 362 | 182 / 360 |
| Latency p50 / p95, misses (ms) | 2,567 / 3,614 | 2,515 / 3,759 | 2,532 / 3,843 |
| Cost without cache (list price) | $1.34 | $13.40 | $26.84 |
| Cost with cache (LLM + embeddings) | $0.53 | $1.54 | $1.91 |
| Saved | $0.81 (60.1%) | $11.86 (88.5%) | $24.93 (92.9%) |

Hit rate within each segment:

- requests 1–1,000: 60.4% (semantic 53.6%)
- requests 1,001–10,000: 91.7% (semantic 76.5%)
- requests 10,001–20,000: 97.2% (semantic 79.7%)

### Wrong hits

11 of 100 sampled semantic hits returned an answer that did not fit the question (11.0%); 0 unsure. Judge: global.anthropic.claude-sonnet-4-6; human-reviewed: 0.

- #1378 "where could i check order 10987 fucking curent status" got the answer to "I'd like information about changing order 10987" (score 0.8602425456047058): The user asked to check the current status of order 10987, but the cached answer addresses how to change or modify the order, not how to track or view its status.
- #1989 "help recovering my profile access key" got the answer to "ineed help to restore the access key of my account" (score 0.7668690085411072): The question asks about recovering a 'profile access key' (likely an API key or similar credential), but the answer addresses password reset, which is a different type of account access issue.
- #5222 "what do I have to do to create a standard account?" got the answer to "i have a problem with creating a Standard account" (score 1.0): The question asks how to create a standard account (steps/instructions), but the cached answer assumes the user is having a problem with account creation and asks troubleshooting questions instead of providing the actual steps.
- #10884 "i acnt see purchase 12045 status" got the answer to "I need help with updating order 12045" (score 1.0): The user asked to check the status/visibility of purchase 12045, but the cached answer is about updating/modifying an order, which is a completely different need.
- #11993 "i do not know how i can see order 10452 current satus" got the answer to "I'd like information about changing order 10987" (score 0.827498197555542): The user wants to check the status of order 10452, but the cached answer is about changing order 10987 and provides no information about how to view order status.
- #12105 "what do I have to do to download the invoice #00108?" got the answer to "i dont know what i need  to do to download my bill #37777" (score 0.9669263362884521): The answer references order #37777 specifically instead of invoice #00108, which could confuse the customer looking for a different invoice.
- #13579 "wanna see pjrchase 11871 damn current status" got the answer to "wanna see pjrchase 11230 damn current status" (score 0.7935463786125183): The answer references order 11230 throughout, but the customer is asking about order 11871, making the order number information incorrect for this question.
- #15088 "how to use the standard account?" got the answer to "i have a problem with creating a Standard account" (score 0.9888060688972473): The question asks how to use a standard account, but the cached answer addresses troubleshooting problems with creating an account, which is a completely different need.
- #16741 "i have got to remove an item from order 11230 help me" got the answer to "i cannot remove some fucking items from order 13377" (score 0.8068138957023621): The answer references order 13377 throughout, but the customer is asking about order 11230, which is a different order number and could cause confusion or lead the customer to look at the wrong order.
- #17703 "would it be possible toplace a fucking order from Seattle" got the answer to "is it possible to order from Denver ?" (score 1.0): The answer confirms Denver/Colorado is in the shipping area, but does not address whether Seattle/Washington is eligible, and a customer from Seattle would be misinformed by the Denver-specific confirmation.
- #18350 "i expect an rebate of CA$$15.50" got the answer to "I am waiting for a rebate of CA$$89" (score 0.8838155269622803): The cached answer references the wrong rebate amount (CA$89 instead of CA$15.50), which would confuse the customer and undermine trust.

### Hit rate at other thresholds (replay, after 20,000)

| Threshold | after 1,000 | after 10,000 | after 20,000 |
|---|---:|---:|---:|
| 0.70 | 64.6% | 89.5% | 93.4% |
| 0.72 | 61.0% | 88.1% | 92.5% |
| 0.74 | 58.0% | 86.6% | 91.5% |
| 0.75 | 56.9% | 85.9% | 90.9% |
| 0.76 | 55.1% | 85.1% | 90.3% |
| 0.78 | 51.4% | 83.2% | 88.9% |
| 0.80 | 48.4% | 81.1% | 87.4% |
| 0.82 | 43.8% | 78.8% | 85.7% |
| 0.84 | 39.6% | 75.9% | 83.4% |
| 0.86 | 34.9% | 73.0% | 81.2% |
| 0.88 | 30.5% | 69.8% | 78.5% |
| 0.90 | 27.4% | 65.8% | 75.3% |
| 0.92 | 23.1% | 61.7% | 71.7% |
| 0.94 | 20.7% | 57.5% | 67.9% |
| 0.96 | 17.7% | 52.8% | 63.5% |
| 0.98 | 16.4% | 49.4% | 59.9% |

Replay check at the applied threshold 0.75: replay 90.9% vs measured 92.9%.

Raw per-request data: `requests.csv` in each run directory.
