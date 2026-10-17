// One runnable check per claim from the Keystone demo-script review.
// Each run returns { status, summary, evidence: [[label, value]], steps: [...] }
// status: CONFIRMED | REFUTED | INCONCLUSIVE | SKIPPED | ERROR
import { randomUUID } from 'node:crypto'
import { createGateway } from './gateway.js'
import { ensureProductVectors, hybridSearch, contextBlock } from './retrieval.js'

// Set from config.questions before every run (runs are serialized in server.js).
let Q1, Q2
function applyQuestions(q) {
  ;({ q1: Q1, q2: Q2 } = q)
}

const sleep = ms => new Promise(r => setTimeout(r, ms))
const newSession = () => randomUUID().slice(0, 8)
const fmt = (n, d = 4) => (n === null || n === undefined || Number.isNaN(n) ? '—' : Number(n).toFixed(d))

// The system prompt is part of the gateway's cache hash (chat_completion.go
// hash(): system_prompt), so a per-run nonce in it isolates every run's
// entries from earlier runs and from other users of the same gateway.
function systemPrompt(sessionId, products = '') {
  const ctx = products ? `\n\nAnswer only from these products:\n${products}` : ''
  return `You are a concise ecommerce shopping assistant. Answer in at most two sentences.${ctx}\n\n[demo-session ${sessionId}]`
}

function result(status, summary, evidence = [], extra = {}) {
  return { status, summary, evidence, ...extra }
}

function makeCtx(cfg) {
  const steps = []
  const gw = createGateway(cfg)
  const record = (label, rec) => {
    steps.push({
      label,
      path: rec.path,
      status: rec.status,
      ms: rec.ms,
      cache: rec.cache ?? null,
      score: rec.score ?? null,
      usage: rec.usage ?? null,
      answer: rec.answer ? rec.answer.slice(0, 400) : null,
      requestHeaders: rec.requestHeaders,
      requestBody: summarizeBody(rec.requestBody),
      responseHeaders: rec.responseHeaders,
    })
    return rec
  }
  const chat = async (label, opts) => record(label, await gw.chat(opts))
  const embed = async (label, input) => record(label, await gw.embed(input))
  return { cfg, gw, steps, chat, embed }
}

function summarizeBody(body) {
  if (!body) return body
  const copy = structuredClone(body)
  if (Array.isArray(copy.messages)) {
    copy.messages = copy.messages.map(m => ({
      ...m,
      content: m.content.length > 300 ? m.content.slice(0, 300) + '…' : m.content,
    }))
  }
  return copy
}

// ---------------------------------------------------------------------------
// Index warm-up: the gateway auto-creates the semantic-cache vector index on
// first use, and a fresh index needs time before lookups return anything.
// ---------------------------------------------------------------------------
let warmup = null
let warmupKey = ''
async function ensureIndexReady(ctx) {
  // A different gateway, key or model means a different cache: warm up again.
  const key = [ctx.cfg.gatewayUrl, ctx.cfg.apiKey, ctx.cfg.chatModel, ctx.cfg.embedModel].join('|')
  if (key !== warmupKey) {
    warmup = null
    warmupKey = key
  }
  if (!warmup) {
    warmup = (async () => {
      const sid = newSession()
      const system = systemPrompt(sid)
      await ctx.chat('warm-up: store', { system, user: 'Which headphones are best on a plane?' })
      const probes = [
        'what headphones should I take on a flight',
        'headphones for air travel',
        'best headset for airplane trips',
        'good headphones for flying long haul',
        'which travel headphones block cabin noise',
        'recommend headphones for a plane journey',
        'airplane headphones recommendation',
        'headphones to use while flying',
      ]
      for (const p of probes) {
        await sleep(ctx.cfg.settleMs)
        const r = await ctx.chat(`warm-up: probe "${p}"`, { system, user: p, threshold: 0.01, debug: true })
        if (r.hit) return true
      }
      return false
    })().catch(err => {
      warmup = null
      throw err
    })
  }
  const ok = await warmup
  if (!ok) {
    warmup = null
    throw new Error(
      'Semantic cache never returned a HIT, even at threshold 0.01. Check that the chat model has ' +
        'semanticCache.isEnabled, the embedding model is reachable, and the vector index exists.',
    )
  }
}

// ---------------------------------------------------------------------------
export const claims = [
  {
    id: 'bedrock-openai',
    title: 'Haiku 4.5 and Titan v2 work through the gateway, in OpenAI shape',
    claim:
      'The gateway serves Bedrock models (serverKind "bedrock") behind /v1/chat/completions and /v1/embeddings. ' +
      'Usage comes back as prompt_tokens / completion_tokens (OpenAI), not input_tokens / output_tokens (Bedrock), ' +
      'and Titan v2 still gives 1024-dim vectors, so the doc\'s product embeddings can stay.',
    docSteps: '1.3, 1.5, 2.2, 2.2T, 2.3T, 2.9, 3.3',
    code: 'docs/bedrock.md; pkg/providers/bedrock/',
    needsCache: false,
    async run(ctx) {
      const models = await ctx.gw.models()
      ctx.steps.push({ label: 'GET /v1/models', path: models.path, status: models.status, ms: models.ms })
      const ids = JSON.stringify(models.responseBody)
      const r = await ctx.chat('chat (cache off)', {
        system: systemPrompt(newSession()),
        user: 'Say hello in five words.',
        cache: 'none',
      })
      const e = await ctx.embed('embedding', Q1)
      const u = r.usage ?? {}
      const openAiUsage = Number.isFinite(u.prompt_tokens) && Number.isFinite(u.completion_tokens)
      const bedrockUsage = 'input_tokens' in u || 'output_tokens' in u
      const dims = e.vector.length
      const evidence = [
        ['Chat model listed in /v1/models', ids.includes(ctx.cfg.chatModel) ? 'yes' : 'no'],
        ['Embedding model listed in /v1/models', ids.includes(ctx.cfg.embedModel) ? 'yes' : 'no'],
        ['Chat usage keys', Object.keys(u).join(', ') || '(none)'],
        ['Embedding dimensions', `${dims} (expected ${ctx.cfg.expectedEmbedDims})`],
        ['Chat latency', `${r.ms} ms`],
      ]
      const ok = openAiUsage && !bedrockUsage && dims === ctx.cfg.expectedEmbedDims
      return result(
        ok ? 'CONFIRMED' : 'REFUTED',
        ok
          ? `Both models answer through the gateway. Usage is OpenAI-shaped (${u.prompt_tokens} prompt / ${u.completion_tokens} completion tokens) and Titan returns ${dims} dims. Rename llm_usage.input_tokens / output_tokens in the doc.`
          : 'The response shape differs from the claim; see evidence.',
        evidence,
      )
    },
  },

  {
    id: 'hit-header',
    title: 'HIT/MISS is the X-Cache header; the score needs X-cb-debug',
    claim:
      'Every chat response carries X-Cache: HIT or MISS. The match score (X-cb-match-score) is only sent when ' +
      'the request has X-cb-debug: true. X-cb-cache is the request header that picks the cache type.',
    docSteps: '2.2, 2.3, 2.6, 2.7',
    code: 'pkg/common/constants.go (HeaderCacheHit = "X-Cache"); chat_completion.go SetCacheDebugHeaders',
    needsCache: true,
    async run(ctx) {
      const system = systemPrompt(newSession())
      const r1 = await ctx.chat('1. first ask (no debug)', { system, user: Q1 })
      await sleep(ctx.cfg.settleMs)
      const r2 = await ctx.chat('2. same question (no debug)', { system, user: Q1 })
      const r3 = await ctx.chat('3. same question (X-cb-debug: true)', { system, user: Q1, debug: true })
      const evidence = [
        ['1 · X-Cache', r1.cache],
        ['2 · X-Cache / X-cb-match-score', `${r2.cache} / ${r2.responseHeaders['x-cb-match-score'] ?? 'absent'}`],
        ['3 · X-Cache / X-cb-match-score', `${r3.cache} / ${r3.responseHeaders['x-cb-match-score'] ?? 'absent'}`],
        ['X-cb-cache in response headers?', 'x-cb-cache' in r2.responseHeaders ? 'yes' : 'no'],
      ]
      const ok =
        r1.cache === 'MISS' &&
        r2.hit &&
        !('x-cb-match-score' in r2.responseHeaders) &&
        r3.hit &&
        'x-cb-match-score' in r3.responseHeaders
      return result(
        ok ? 'CONFIRMED' : 'REFUTED',
        ok
          ? 'X-Cache reports HIT/MISS on every call; the score only appears with X-cb-debug: true. Scripts must read X-Cache, not X-cb-cache.'
          : 'Header behaviour differs from the claim; see evidence.',
        evidence,
      )
    },
  },


  {
    id: 'system-prompt-key',
    title: 'The system prompt is part of the cache key',
    claim:
      'Semantic search only considers entries with the same hash, and the hash covers the system prompt and ' +
      'model settings (not the messages). If the RAG context goes in the system prompt, a different top-3 ' +
      'means no HIT, even for the identical question.',
    docSteps: '2.3, 2.7, 2.8 (RAG prompt design)',
    code: 'pkg/handlers/types/chat_completion.go Hash()/hash(): system_prompt; pkg/cache/v2/cache.go knn filter on hash',
    needsCache: true,
    async run(ctx) {
      const sid = newSession()
      const products = [
        '- prod_003 | Zephyr Quiet-Ride Travel Headset | $249.5',
        '- prod_002 | Kestrel Studio 900 Over-Ear Headphones | $299',
        '- prod_015 | Aria Hush Noise-Cancelling Earbuds | $199',
      ]
      const sysA = systemPrompt(sid, products.join('\n'))
      const sysB = systemPrompt(sid, [products[1], products[0], products[2]].join('\n'))
      await ctx.chat('1. store: context A, question 1', { system: sysA, user: Q1 })
      await sleep(ctx.cfg.settleMs)
      const r2 = await ctx.chat('2. SAME question, context B (same products, different order), threshold 0.01', {
        system: sysB,
        user: Q1,
        threshold: 0.01,
        debug: true,
      })
      const r3 = await ctx.chat('3. reworded question, context A, threshold 0.01', {
        system: sysA,
        user: Q2,
        threshold: 0.01,
        debug: true,
      })
      const evidence = [
        ['2 · identical question, reordered context', `${r2.cache}${r2.score !== null ? ` (score ${fmt(r2.score)})` : ''}`],
        ['3 · reworded question, same context', `${r3.cache}${r3.score !== null ? ` (score ${fmt(r3.score)})` : ''}`],
      ]
      const ok = !r2.hit && r3.hit
      return result(
        ok ? 'CONFIRMED' : r2.hit ? 'REFUTED' : 'INCONCLUSIVE',
        ok
          ? 'Reordering the context in the system prompt turned an identical question into a MISS, even at threshold 0.01. The same context with a reworded question HIT. Keep retrieval output out of the system prompt, or accept that differing retrieval means no reuse.'
          : r2.hit
            ? 'An identical question with a different system prompt still hit, so the system prompt is not in the key here.'
            : 'Step 3 missed too, so this run cannot separate the two effects.',
        evidence,
      )
    },
  },

  {
    id: 'usage-on-hit',
    title: 'A HIT returns the original usage block',
    claim:
      'The cached response body is replayed as stored, so a HIT carries the MISS\'s usage numbers. A script ' +
      'that adds up usage without checking X-Cache counts tokens it never paid for.',
    docSteps: '2.2T, 2.3T, 2.7, 2.8, 2.9',
    code: 'pkg/cache/v2/cache.go getChatCompletion (returns cacheRecord.Response); chat_completion_handler.go trackCachedPromptTokens',
    needsCache: true,
    async run(ctx) {
      const system = systemPrompt(newSession())
      const r1 = await ctx.chat('1. MISS', { system, user: Q1 })
      await sleep(ctx.cfg.settleMs)
      const r2 = await ctx.chat('2. HIT (same question)', { system, user: Q1 })
      const u1 = r1.usage ?? {}
      const u2 = r2.usage ?? {}
      const evidence = [
        ['1 · X-Cache / usage', `${r1.cache} / ${u1.prompt_tokens} in, ${u1.completion_tokens} out`],
        ['2 · X-Cache / usage', `${r2.cache} / ${u2.prompt_tokens} in, ${u2.completion_tokens} out`],
        ['Latency MISS → HIT', `${r1.ms} ms → ${r2.ms} ms`],
      ]
      if (!r2.hit) return result('INCONCLUSIVE', 'The repeat did not HIT.', evidence)
      const same = u2.prompt_tokens === u1.prompt_tokens && u2.completion_tokens === u1.completion_tokens
      const nonzero = (u2.prompt_tokens ?? 0) > 0
      return result(
        same && nonzero ? 'CONFIRMED' : 'REFUTED',
        same && nonzero
          ? 'The HIT reported the same usage as the MISS, although Claude was not called. Count tokens only when X-Cache is MISS; use the cached_* metrics for savings.'
          : 'The HIT reported different or zero usage; see evidence.',
        evidence,
      )
    },
  },


  {
    id: 'retrieval-on-hit',
    title: 'A gateway HIT still pays for embedding + retrieval',
    claim:
      'The gateway only caches the Claude call. In a RAG app the client embeds the question and runs hybrid ' +
      'search before calling the gateway, on every request, so a HIT saves only the LLM time. The doc\'s ' +
      '~1.2 s HIT that skips retrieval is not what the gateway does.',
    docSteps: '2.3, 2.5, 2.7, 2.8 (latency tables)',
    code: 'pkg/handlers/chat_completion_handler.go (cache lookup happens per chat request)',
    needsCache: true,
    async run(ctx) {
      const warm = await ensureProductVectors(ctx.gw, `${ctx.cfg.gatewayUrl}|${ctx.cfg.embedModel}`)
      const sid = newSession()
      const turn = async (label, q) => {
        const e = await ctx.embed(`${label}: embed question`, q)
        const r = hybridSearch(q, e.vector)
        const c = await ctx.chat(`${label}: chat with top-3 context`, {
          system: systemPrompt(sid, contextBlock(r.top)),
          user: q,
        })
        return { e, r, c, ids: r.top.map(p => p.product_id).join(', ') }
      }
      const t1 = await turn('1. MISS', Q1)
      await sleep(ctx.cfg.settleMs)
      const t2 = await turn('2. same question again', Q1)
      const t3 = await turn('3. reworded question', Q2)
      const row = t =>
        `embed ${t.e.ms} ms + retrieve ${fmt(t.r.ms, 1)} ms + gateway ${t.c.ms} ms (${t.c.cache}) · top-3 ${t.ids}`
      const evidence = [
        ['Product vectors', warm.cached ? 'already embedded' : `15 embedded in ${warm.ms} ms`],
        ['1 · ' + Q1, row(t1)],
        ['2 · ' + Q1, row(t2)],
        ['3 · ' + Q2, row(t3)],
        ['Doc reference', 'Titan embed ≈ 198 ms, hybrid retrieval + fetch ≈ 818 ms (Table 2.8)'],
      ]
      if (!t2.c.hit) return result('INCONCLUSIVE', 'The repeated question did not HIT.', evidence)
      const paidOnHit = t2.e.ms + t2.r.ms
      return result(
        'CONFIRMED',
        `On the HIT the client still spent ${Math.round(paidOnHit)} ms embedding and retrieving before the gateway answered in ${t2.c.ms} ms (vs ${t1.c.ms} ms on the MISS). Against Couchbase FTS that retrieval is ~800 ms more. Step 3 shows the rewording ${t3.ids === t1.ids ? 'kept the same top-3' : 'changed the top-3'}, which ${t3.c.hit ? 'still hit' : 'missed'}.`,
        evidence,
      )
    },
  },




]

export async function runClaim(id, cfg) {
  const claim = claims.find(c => c.id === id)
  if (!claim) throw new Error(`Unknown claim ${id}`)
  if (!cfg.apiKey) return result('SKIPPED', 'No API key: set it in Preferences → Gateway.', [], { steps: [] })
  applyQuestions(cfg.questions)
  const ctx = makeCtx(cfg)
  const t0 = Date.now()
  try {
    if (claim.needsCache) await ensureIndexReady(ctx)
    const r = await claim.run(ctx)
    return { ...r, steps: ctx.steps, ms: Date.now() - t0 }
  } catch (err) {
    if (err.call) ctx.steps.push({ label: 'failed call', ...err.call })
    return { status: 'ERROR', summary: err.message, evidence: [], steps: ctx.steps, ms: Date.now() - t0 }
  }
}

export function claimList() {
  return claims.map(({ id, title, claim, docSteps, code }, i) => ({ n: i + 1, id, title, claim, docSteps, code }))
}
