// Starts the mock gateway and the demo server, runs every claim through the
// HTTP API and fails if any claim errors. Plumbing check only.
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const MOCK = 18081
const APP = 13000
const envFile = join(mkdtempSync(join(tmpdir(), 'cache-demo-')), '.env')
writeFileSync(envFile, '# smoke\nSETTLE_MS=50\n')
const env = {
  ...process.env,
  ENV_FILE: envFile,
  RESULTS_FILE: join(envFile, '..', 'claim-results.json'),
  MOCK_PORT: String(MOCK),
  PORT: String(APP),
  GATEWAY_URL: `http://localhost:${MOCK}`,
  GATEWAY_API_KEY: 'mock-key-1',
  GATEWAY_API_KEY_2: 'mock-key-2',
  INSECURE_TLS: 'false',
  CHAT_MODEL: 'mock-chat',
  EMBED_MODEL: 'mock-embed',
  EXPECTED_EMBED_DIMS: '1024',
  METRICS_URL: `http://localhost:${MOCK}/metrics`,
  METRICS_PREFIX: 'ai_model_service_gateway_',
  SETTLE_MS: '50',
  CB_CONNECTION_STRING: '',
}
const root = new URL('..', import.meta.url).pathname
const procs = [
  spawn('node', ['test/mock-gateway.js'], { cwd: root, env, stdio: 'inherit' }),
  spawn('node', ['server.js'], { cwd: root, env, stdio: 'inherit' }),
]
const stop = code => {
  procs.forEach(p => p.kill())
  process.exit(code)
}

const wait = ms => new Promise(r => setTimeout(r, ms))
await wait(1000)
const base = `http://localhost:${APP}`
const claims = await (await fetch(`${base}/api/claims`)).json()
let failed = 0
for (const c of claims) {
  const r = await (await fetch(`${base}/api/claims/${c.id}/run`, { method: 'POST' })).json()
  console.log(`${r.status.padEnd(12)} ${c.id.padEnd(18)} ${r.summary}`)
  if (r.status === 'ERROR') failed++
}
// Preferences: invalid input is rejected, valid input applies and persists.
const put = body =>
  fetch(`${base}/api/preferences`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const bad = await put({ SETTLE_MS: 'abc', GATEWAY_URL: 'not a url' })
const badBody = await bad.json()
if (bad.status !== 400 || !badBody.errors.SETTLE_MS || !badBody.errors.GATEWAY_URL) {
  console.log('FAIL prefs validation', badBody)
  failed++
}
const good = await (await put({ SETTLE_MS: '75', Q1: 'quiet headphones for a red-eye flight', GATEWAY_API_KEY: '' })).json()
const cfg = await (await fetch(`${base}/api/config`)).json()
const pref = k => cfg.prefs.find(p => p.key === k)
const saved = readFileSync(envFile, 'utf8')
const prefsOk =
  good.ok &&
  pref('SETTLE_MS').value === 75 &&
  pref('GATEWAY_API_KEY').isSet &&
  pref('GATEWAY_API_KEY').value === undefined &&
  saved.includes('SETTLE_MS=75') &&
  saved.includes('Q1="quiet headphones for a red-eye flight"') &&
  !saved.includes('mock-key-1') &&
  !JSON.stringify(cfg).includes('mock-key-1')
console.log(`${prefsOk ? 'OK' : 'FAIL'}          preferences        validate, apply, persist, secrets never returned`)
if (!prefsOk) failed++
const rerun = await (await fetch(`${base}/api/claims/hit-header/run`, { method: 'POST' })).json()
const usedNewQ = rerun.steps.some(s => JSON.stringify(s.requestBody ?? {}).includes('red-eye'))
console.log(`${usedNewQ ? 'OK' : 'FAIL'}          edited question    used by the next run (${rerun.status})`)
if (!usedNewQ) failed++
const results = await (await fetch(`${base}/api/results`)).json()
if (!results['hit-header']) failed++

const html = await (await fetch(base)).text()
if (!html.includes('id="claims"')) failed++

// Restart the server: preferences (from .env) and claim results (from disk) reload.
procs[1].kill()
await wait(500)
// Without the env overrides, so the saved .env is what loads.
const env2 = { ...env }
delete env2.SETTLE_MS
delete env2.Q1
procs[1] = spawn('node', ['server.js'], { cwd: root, env: env2, stdio: 'inherit' })
await wait(1500)
const after = await (await fetch(`${base}/api/results`)).json()
const cfg2 = await (await fetch(`${base}/api/config`)).json()
const reloadOk =
  after['hit-header']?.status === results['hit-header'].status &&
  Object.keys(after).length === Object.keys(results).length &&
  cfg2.prefs.find(p => p.key === 'SETTLE_MS').value === 75
console.log(`${reloadOk ? 'OK' : 'FAIL'}          restart            claim results and preferences reload`)
if (!reloadOk) failed++
console.log(failed ? `\n${failed} problem(s)` : '\nsmoke OK (mock gateway: plumbing only, not proof of the claims)')
stop(failed ? 1 : 0)
