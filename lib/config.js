import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'

export const envFile = process.env.ENV_FILE || fileURLToPath(new URL('../.env', import.meta.url))
dotenv.config({ path: envFile })

const env = (name, fallback = '') => (process.env[name] ?? fallback).trim()

// One entry per editable preference: env var, config path, type, group, label.
// `secret` values are write-only from the UI: never sent back, blank = unchanged.
export const PREFS = [
  { key: 'GATEWAY_URL', path: 'gatewayUrl', type: 'url', group: 'Gateway', label: 'Gateway URL', default: 'https://localhost:8081' },
  { key: 'GATEWAY_API_KEY', path: 'apiKey', type: 'text', secret: true, group: 'Gateway', label: 'API key' },
  { key: 'INSECURE_TLS', path: 'insecureTls', type: 'bool', group: 'Gateway', label: 'Accept self-signed TLS', default: 'false' },
  { key: 'CHAT_MODEL', path: 'chatModel', type: 'text', group: 'Models', label: 'Chat model id', default: 'bedrock-global-anthropic-claude-haiku-4-5-20251001-v1-0' },
  { key: 'EMBED_MODEL', path: 'embedModel', type: 'text', group: 'Models', label: 'Embedding model id', default: 'bedrock-amazon-titan-embed-text-v2-0' },
  { key: 'EXPECTED_EMBED_DIMS', path: 'expectedEmbedDims', type: 'int', min: 1, max: 8192, group: 'Models', label: 'Expected embedding dims', default: '1024' },
  { key: 'SONNET_MODEL', path: 'sonnetModel', type: 'text', group: 'Savings test', label: 'Sonnet 4.6 model id', default: 'bedrock-global-anthropic-claude-sonnet-4-6' },
  { key: 'JUDGE_MODEL', path: 'judgeModel', type: 'text', group: 'Savings test', label: 'Wrong-hit judge model id', default: 'bedrock-global-anthropic-claude-sonnet-4-6' },
  { key: 'SETTLE_MS', path: 'settleMs', type: 'int', min: 0, max: 60000, group: 'Timing', label: 'Pause after each cache store (ms)', default: '2500' },
  { key: 'Q1', path: 'questions.q1', type: 'text', group: 'Questions', label: 'Q1: first question (stored)', default: 'quiet headphones for flying' },
  { key: 'Q2', path: 'questions.q2', type: 'text', group: 'Questions', label: 'Q2: rewording of Q1', default: 'noise cancelling headphones for a long flight' },
]

function coerce(pref, raw) {
  const v = String(raw ?? '').trim()
  switch (pref.type) {
    case 'bool':
      return v === 'true'
    case 'int':
      return Number(v)
    case 'url':
      return v.replace(/\/+$/, '')
    default:
      return v
  }
}

function setPath(obj, path, value) {
  const parts = path.split('.')
  const last = parts.pop()
  let o = obj
  for (const p of parts) o = o[p] ??= {}
  o[last] = value
}

function getPath(obj, path) {
  return path.split('.').reduce((o, p) => o?.[p], obj)
}

export const config = {
  questions: {},
  host: env('HOST', '127.0.0.1'),
  port: Number(env('PORT', '4000')),
}
for (const p of PREFS) setPath(config, p.path, coerce(p, env(p.key, p.default ?? '')))

function applyTls() {
  // Local gateways use self-signed certs (make gen-certs). Opt-in only.
  if (config.insecureTls) process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'
  else delete process.env.NODE_TLS_REJECT_UNAUTHORIZED
}
applyTls()

// What the UI is allowed to see: never the secrets.
export function publicConfig() {
  return {
    prefs: PREFS.map(p => ({
      key: p.key,
      group: p.group,
      label: p.label,
      type: p.type,
      secret: Boolean(p.secret),
      optional: Boolean(p.optional),
      min: p.min,
      max: p.max,
      value: p.secret ? undefined : getPath(config, p.path),
      isSet: Boolean(getPath(config, p.path)),
    })),
    envFile,
  }
}

// Validate and apply { KEY: value } from the UI, then persist to the .env file.
// Secrets: '' or absent keeps the current value; null clears it.
export function updatePreferences(input) {
  const errors = {}
  const next = {}
  for (const p of PREFS) {
    if (!(p.key in input)) continue
    const raw = input[p.key]
    if (p.secret && (raw === '' || raw === undefined)) continue
    const str = raw === null ? '' : String(raw).trim()
    if (p.type === 'url' && str && !/^https?:\/\/[^\s]+$/.test(str)) errors[p.key] = 'Must be an http(s) URL'
    else if (p.type === 'int') {
      const n = Number(str)
      if (!Number.isInteger(n) || n < p.min || n > p.max) errors[p.key] = `Whole number ${p.min}–${p.max}`
    } else if (!str && !p.optional && !p.secret) errors[p.key] = 'Required'
    else if (/[\r\n]/.test(str)) errors[p.key] = 'Must be a single line'
    next[p.key] = str
  }
  if (Object.keys(errors).length) return { ok: false, errors }

  const changed = []
  for (const p of PREFS) {
    if (!(p.key in next)) continue
    const value = coerce(p, next[p.key])
    if (getPath(config, p.path) !== value) changed.push(p.key)
    setPath(config, p.path, value)
  }
  applyTls()
  writeEnv(next)
  return { ok: true, changed }
}

// Rewrite only the given keys in the .env file, keeping comments and order.
function writeEnv(values) {
  const lines = existsSync(envFile) ? readFileSync(envFile, 'utf8').split('\n') : []
  const pending = new Map(Object.entries(values))
  const quote = v => (/[\s#"']/.test(v) ? JSON.stringify(v) : v)
  const out = lines.map(line => {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=/)
    if (m && pending.has(m[1])) {
      const v = pending.get(m[1])
      pending.delete(m[1])
      return `${m[1]}=${quote(v)}`
    }
    return line
  })
  if (out.length && out[out.length - 1] === '') out.pop()
  for (const [k, v] of pending) out.push(`${k}=${quote(v)}`)
  writeFileSync(envFile, out.join('\n') + '\n', { mode: 0o600 })
}
