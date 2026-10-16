// Starts the mock gateway and the demo server, then drives the savings page's
// API: a short chatbot run, judge, a human review, report and summary.
// Plumbing check only; needs the chatbot dataset (npm run savings:setup, then
// build it on the page or with `python -m savings datasets --workload chatbot`).
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const MOCK = 18082
const APP = 13001
const root = new URL('..', import.meta.url).pathname
if (!existsSync(join(root, 'savings/data/chatbot-meta.json'))) {
  console.error('Build the chatbot dataset first: cd savings && .venv/bin/python -m savings datasets --workload chatbot')
  process.exit(2)
}
const tmp = mkdtempSync(join(tmpdir(), 'savings-smoke-'))
const envFile = join(tmp, '.env')
writeFileSync(envFile, '# smoke\n')
const env = {
  ...process.env,
  ENV_FILE: envFile,
  PORT_FALLBACK: 'false',
  OPEN_BROWSER: 'false',
  RESULTS_FILE: join(envFile, '..', 'claim-results.json'),
  MOCK_PORT: String(MOCK),
  MOCK_STORE_EMBEDDING: 'query',
  PORT: String(APP),
  GATEWAY_URL: `http://localhost:${MOCK}`,
  GATEWAY_API_KEY: 'mock-key-1',
  INSECURE_TLS: 'false',
  CHAT_MODEL: 'mock-chat',
  SONNET_MODEL: 'mock-chat',
  JUDGE_MODEL: 'mock-chat',
  EMBED_MODEL: 'mock-embed',
  SAVINGS_RUNS_DIR: join(tmp, 'runs'),
}
const procs = [
  spawn('node', ['test/mock-gateway.js'], { cwd: root, env, stdio: 'inherit' }),
  spawn('node', ['server.js'], { cwd: root, env, stdio: 'inherit' }),
]
const stop = code => {
  procs.forEach(p => p.kill())
  process.exit(code)
}
process.on('uncaughtException', err => {
  console.error(err)
  stop(1)
})
process.on('unhandledRejection', err => {
  console.error(err)
  stop(1)
})
const wait = ms => new Promise(r => setTimeout(r, ms))
const base = `http://localhost:${APP}/api/savings`
const call = async (path, method = 'GET', body) => {
  const res = await fetch(base + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const ct = res.headers.get('content-type') ?? ''
  return { status: res.status, body: ct.includes('json') ? await res.json() : await res.text() }
}
const check = (ok, msg) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${msg}`)
  if (!ok) stop(1)
}
const until = async (fn, ms = 60000) => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) {
    const v = await fn()
    if (v) return v
    await wait(500)
  }
  return null
}

const ready = await until(async () => {
  try {
    return (await call('/state')).status === 200
  } catch {
    return false
  }
}, 15000)
check(ready, 'server up')
let s = (await call('/state')).body
check(s.datasets.chatbot.meta !== null, 'chatbot dataset visible')
check((await call('/runs', 'POST', { workload: 'nope', model: 'sonnet' })).status === 400, 'bad workload rejected')
const r = await call('/runs', 'POST', { workload: 'chatbot', model: 'sonnet', runId: 'smoke', limit: 300, concurrency: 8 })
check(r.status === 200, `run started: ${JSON.stringify(r.body).slice(0, 120)}`)
check((await call('/runs', 'POST', { workload: 'chatbot', model: 'sonnet', runId: 'smoke' })).status === 409, 'duplicate run id rejected')
const done = await until(async () => {
  const st = (await call('/state')).body
  const run = st.runs.find(x => x.runId === 'smoke')
  return run && run.status === 'complete' && run.hasReport ? run : null
})
check(done, `run complete: ${done?.progress?.done} requests, hit rate ${done?.progress?.hit_rate}`)
check((await call('/runs/nope/judge', 'POST', { sample: 5 })).status === 404, 'judge on unknown run refused')
const j = await call('/runs/smoke/judge', 'POST', { sample: 20 })
check(j.status === 200, 'judge started')
const judged = await until(async () => {
  const rv = (await call('/runs/smoke/review')).body
  return rv.items.length ? rv : null
})
check(judged && judged.items.length === 20 && judged.items[0].answer, `judged ${judged?.items.length} semantic hits with answers`)
const seq = judged.items[0].seq
check((await call('/runs/smoke/review', 'PUT', { seq, verdict: 'wrong' })).status === 200, 'human verdict saved')
check((await call('/runs/smoke/review', 'PUT', { seq, verdict: 'bogus' })).status === 400, 'bad verdict rejected')
await until(async () => !(await call('/state')).body.runs[0].jobs.some(x => x.state === 'running'))
check((await call('/runs/smoke/report', 'POST')).status === 200, 'report rebuild started')
const rep = await until(async () => {
  const st = (await call('/state')).body
  const run = st.runs.find(x => x.runId === 'smoke')
  return run.wrongHits && run.jobs.every(x => x.state !== 'running') ? run : null
})
check(rep && rep.wrongHits.wrong >= 1, `report counts the human verdict: ${JSON.stringify(rep?.wrongHits)}`)
const html = await call('/files/smoke/report.html')
check(html.status === 200 && html.body.includes('Measures 1–4'), 'report.html served')
const csv = await call('/files/smoke/requests.csv')
check(csv.status === 200 && csv.body.startsWith('seq,'), 'requests.csv served')
const header = csv.body.split(/\r?\n/)[0].split(',')
check(header.at(-1) === 'response' && csv.body.includes('Mock answer'), 'requests.csv has the response text')
check((await call('/files/smoke/..%2F..%2Fpackage.json')).status === 404, 'path traversal refused')
check((await call('/summary', 'POST', { runs: ['smoke'] })).status === 200, 'summary started')
const sum = await until(async () => ((await call('/state')).body.summary.exists ? true : null))
check(sum, 'summary built')
const sh = await call('/files/summary/summary.html')
check(sh.status === 200 && sh.body.includes('Keystone cache savings test'), 'summary.html served')
// Restart the UI server: runs, reports, reviews and job history reload from disk.
await new Promise(r => {
  procs[1].once('exit', r)
  procs[1].kill()
})
procs[1] = spawn('node', ['server.js'], { cwd: root, env, stdio: 'inherit' })
const back = await until(async () => {
  try {
    return (await call('/state')).body
  } catch {
    return null
  }
}, 15000)
const run2 = back?.runs.find(x => x.runId === 'smoke')
check(run2?.status === 'complete' && run2.hasReport && run2.wrongHits?.wrong >= 1, 'after restart: run, report and review reload')
check(run2?.jobs.some(x => x.key === 'judge:smoke' && x.state === 'done'), 'after restart: job history reloads')
check(back?.summary.exists, 'after restart: summary still there')
const rv2 = (await call('/runs/smoke/review')).body
check(rv2.items.find(i => i.seq === seq)?.human === 'wrong', 'after restart: human verdict kept')
console.log(`runs dir: ${env.SAVINGS_RUNS_DIR}`)
stop(0)
