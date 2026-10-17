// Thin client for the AI Gateway's OpenAI-compatible API.
// Header names come from ai-gateway pkg/common/constants.go.
import { performance } from 'node:perf_hooks'

export const H = {
  cacheType: 'X-cb-cache', // request: none | standard | semantic
  cacheThreshold: 'X-cb-cache-threshold', // request: float override
  cacheExpiry: 'X-cb-cache-expiry-duration', // request: seconds
  debug: 'X-cb-debug', // request: "true" enables debug response headers
  cacheHit: 'x-cache', // response: HIT | MISS (always sent)
  matchScore: 'x-cb-match-score', // response: only on HIT with debug
  matchThreshold: 'x-cb-semantic-cache-match-threshold', // response: debug only
}

export class GatewayError extends Error {
  constructor(message, call) {
    super(message)
    this.call = call
  }
}

const interestingHeader = name => name === 'x-cache' || name.startsWith('x-cb-')

export function createGateway(cfg, apiKey = cfg.apiKey) {
  async function call(path, { method = 'POST', body, headers = {} } = {}) {
    const reqHeaders = {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
      ...headers,
    }
    const t0 = performance.now()
    let res
    try {
      res = await fetch(cfg.gatewayUrl + path, {
        method,
        headers: reqHeaders,
        body: body ? JSON.stringify(body) : undefined,
      })
    } catch (err) {
      throw new GatewayError(`Cannot reach ${cfg.gatewayUrl}${path}: ${err.cause?.message ?? err.message}`, {
        path,
      })
    }
    const text = await res.text()
    const ms = performance.now() - t0
    let json = null
    try {
      json = JSON.parse(text)
    } catch {
      // keep raw text
    }
    const respHeaders = {}
    for (const [k, v] of res.headers) {
      if (interestingHeader(k)) respHeaders[k] = v
    }
    const record = {
      path,
      status: res.status,
      ms: Math.round(ms),
      requestHeaders: Object.fromEntries(
        Object.entries(reqHeaders).filter(([k]) => k !== 'Authorization'),
      ),
      requestBody: body,
      responseHeaders: respHeaders,
      responseBody: json ?? text.slice(0, 2000),
    }
    if (!res.ok) {
      const msg = json?.error?.message ?? json?.message ?? text.slice(0, 300)
      throw new GatewayError(`${path} returned ${res.status}: ${msg}`, record)
    }
    return record
  }

  async function chat({
    system,
    user,
    cache = 'semantic',
    threshold,
    expiry,
    debug = false,
    maxTokens = 120,
  }) {
    const headers = { [H.cacheType]: cache }
    if (debug) headers[H.debug] = 'true'
    if (threshold !== undefined) headers[H.cacheThreshold] = String(threshold)
    if (expiry !== undefined) headers[H.cacheExpiry] = String(expiry)
    const messages = []
    if (system) messages.push({ role: 'system', content: system })
    messages.push({ role: 'user', content: user })
    const rec = await call('/v1/chat/completions', {
      headers,
      body: {
        model: cfg.chatModel,
        messages,
        temperature: 0,
        max_tokens: maxTokens,
      },
    })
    const body = rec.responseBody ?? {}
    const score = rec.responseHeaders[H.matchScore]
    return {
      ...rec,
      cache: rec.responseHeaders[H.cacheHit] ?? '(no X-Cache header)',
      hit: rec.responseHeaders[H.cacheHit] === 'HIT',
      score: score === undefined ? null : Number(score),
      answer: body.choices?.[0]?.message?.content ?? '',
      usage: body.usage ?? null,
    }
  }

  async function embed(input) {
    const rec = await call('/v1/embeddings', {
      body: { model: cfg.embedModel, input },
    })
    const vector = rec.responseBody?.data?.[0]?.embedding
    if (!Array.isArray(vector)) {
      throw new GatewayError('Embedding response has no data[0].embedding array', rec)
    }
    return { ...rec, vector, usage: rec.responseBody.usage ?? null }
  }

  async function models() {
    return call('/v1/models', { method: 'GET' })
  }

  return { call, chat, embed, models }
}

export const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0)
export const norm = a => Math.sqrt(dot(a, a))
export const cosine = (a, b) => dot(a, b) / (norm(a) * norm(b))
