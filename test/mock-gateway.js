// PLUMBING MOCK ONLY. It imitates the gateway behaviour read from the
// ai-gateway code so the demo app can be exercised without AWS or Couchbase.
// A run against this mock proves the app works, not that the claims hold;
// only a run against a real gateway does that.
import http from 'node:http'
import { createHash } from 'node:crypto'

const PORT = Number(process.env.MOCK_PORT ?? 18081)
const DIMS = 1024
const DEFAULT_THRESHOLD = 0.45
const PREFIX = 'ai_model_service_gateway_'
const entries = [] // { key, hash, respEmbedding, response, expiresAt }
const counters = {}

const bump = (name, model, by = 1) => {
  const k = `${PREFIX}${name}{model_id="${model}",model_name="mock",batch="false"}`
  counters[k] = (counters[k] ?? 0) + by
}

function embed(text) {
  const v = new Array(DIMS).fill(0)
  for (const w of text.toLowerCase().match(/[a-z0-9]+/g) ?? []) {
    const h = createHash('md5').update(w).digest()
    v[h.readUInt16BE(0) % DIMS] += 1
  }
  const n = Math.sqrt(v.reduce((s, x) => s + x * x, 0)) || 1
  return v.map(x => x / n)
}
const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0)
const sha = s => createHash('sha256').update(s).digest('hex')

function chat(req, body, auth) {
  const h = name => req.headers[name.toLowerCase()]
  const debug = h('X-cb-debug') === 'true'
  const cacheType = h('X-cb-cache') ?? 'standard'
  const system = body.messages.find(m => m.role === 'system')?.content ?? ''
  const user = [...body.messages].reverse().find(m => m.role === 'user')?.content ?? ''
  const hash = sha(JSON.stringify({ model: body.model, system, auth }))
  const key = sha(JSON.stringify({ hash, messages: body.messages }))
  const threshold = h('X-cb-cache-threshold') ? Number(h('X-cb-cache-threshold')) : DEFAULT_THRESHOLD
  const expiry = Number(h('X-cb-cache-expiry-duration') ?? 3600)
  const now = Date.now()
  const live = entries.filter(e => e.expiresAt > now)
  const headers = {}
  if (debug) {
    headers['X-cb-semantic-cache-match-threshold'] = String(threshold)
    headers['X-cb-cache-expiry'] = `${expiry}s`
  }

  let hit = null
  if (cacheType !== 'none') {
    const exact = live.find(e => e.key === key)
    if (exact) hit = { e: exact, score: 1 }
    else if (cacheType === 'semantic') {
      const q = embed(user)
      const best = live
        .filter(e => e.hash === hash)
        .map(e => ({ e, score: dot(q, e.respEmbedding) }))
        .sort((a, b) => b.score - a.score)[0]
      if (best && best.score >= threshold) hit = best
    }
  }
  if (hit) {
    headers['X-Cache'] = 'HIT'
    if (debug) headers['X-cb-match-score'] = String(hit.score)
    bump('cache_hits', body.model)
    bump(cacheType === 'semantic' ? 'semantic_cache_hits' : 'standard_cache_hits', body.model)
    bump('cached_prompt_tokens', body.model, hit.e.response.usage.prompt_tokens)
    bump('cached_completion_tokens', body.model, hit.e.response.usage.completion_tokens)
    return { headers, json: hit.e.response, delay: 30 }
  }
  headers['X-Cache'] = 'MISS'
  const answer = `Mock answer: for "${user}" try the Zephyr Quiet-Ride Travel Headset.`
  const usage = { prompt_tokens: 40 + system.length / 4 | 0, completion_tokens: 25, total_tokens: 0 }
  usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
  const response = {
    id: 'mock-' + key.slice(0, 8),
    object: 'chat.completion',
    model: body.model,
    choices: [{ index: 0, message: { role: 'assistant', content: answer }, finish_reason: 'stop' }],
    usage,
  }
  if (cacheType !== 'none') {
    bump('cache_misses', body.model)
    // NIM stores the answer's embedding; Titan and others reuse the query's
    // (ai-gateway metadata.go GetPassageEmbeddings). MOCK_STORE_EMBEDDING=query
    // imitates the latter for the savings harness.
    const stored = process.env.MOCK_STORE_EMBEDDING === 'query' ? embed(user) : embed(answer)
    entries.push({ key, hash, respEmbedding: stored, response, expiresAt: now + expiry * 1000 })
  }
  bump('prompt_tokens', body.model, usage.prompt_tokens)
  bump('completion_tokens', body.model, usage.completion_tokens)
  return { headers, json: response, delay: 300 }
}

http
  .createServer((req, res) => {
    let raw = ''
    req.on('data', c => (raw += c))
    req.on('end', () => {
      const send = (status, json, headers = {}, delay = 0) =>
        setTimeout(() => {
          res.writeHead(status, { 'Content-Type': 'application/json', ...headers })
          res.end(typeof json === 'string' ? json : JSON.stringify(json))
        }, delay)
      if (req.url === '/metrics') {
        const lines = Object.entries(counters).map(([k, v]) => `${k} ${v}`)
        return send(200, lines.join('\n') + '\n', { 'Content-Type': 'text/plain' })
      }
      const auth = req.headers.authorization ?? ''
      if (!auth.startsWith('Bearer ')) return send(401, { error: { message: 'missing key' } })
      const body = raw ? JSON.parse(raw) : {}
      if (req.url === '/v1/models') {
        return send(200, { data: [{ id: 'mock-chat' }, { id: 'mock-embed' }] })
      }
      if (req.url === '/v1/embeddings') {
        const n = Math.ceil(String(body.input).length / 4)
        return send(200, { data: [{ index: 0, embedding: embed(body.input) }], usage: { prompt_tokens: n, total_tokens: n } }, {}, 20)
      }
      if (req.url === '/v1/chat/completions') {
        const r = chat(req, body, auth)
        return send(200, r.json, r.headers, r.delay)
      }
      send(404, { error: { message: 'not found' } })
    })
  })
  .listen(PORT, () => console.log(`mock gateway on http://localhost:${PORT} (plumbing only)`))
