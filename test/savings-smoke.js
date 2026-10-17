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
check(header[header.indexOf('question') + 1] === 'response' && header.at(-1) === 'response_id' && csv.body.includes('Mock answer'),
  'requests.csv has the response text beside the question')
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

// Zip downloads: share (report, CSV, README) and full (restorable, no job.json).
const zipOf = async q => {
  const res = await fetch(`${base}/runs/smoke/zip?${q}`)
  const buf = Buffer.from(await res.arrayBuffer())
  return { status: res.status, name: res.headers.get('content-disposition') ?? '', buf, text: buf.toString('latin1') }
}
const zs = await zipOf('kind=share')
check(zs.status === 200 && zs.buf.subarray(0, 2).toString() === 'PK' && zs.name.includes('smoke-share.zip'), 'share zip downloads')
check(['smoke/report.html', 'smoke/requests.csv', 'smoke/README.txt', 'smoke/judge.jsonl'].every(f => zs.text.includes(f)) && !zs.text.includes('smoke/embeds.f32'), 'share zip has report, CSV, README, judge; no vectors')
const zf = await zipOf('kind=full&vectors=0')
check(zf.status === 200 && zf.name.includes('smoke-full-no-vectors.zip') && zf.text.includes('smoke/meta.json') && !zf.text.includes('smoke/job.json') && !zf.text.includes('smoke/shadow.f32'), 'full zip without vectors: restorable files, no job.json')
check((await fetch(`${base}/runs/nope/zip?kind=share`)).status === 404, 'zip of unknown run: 404')

// Deleting a run: refused while it runs, then removes it and its files.
const r2 = await call('/runs', 'POST', { workload: 'chatbot', model: 'sonnet', runId: 'smoke2', limit: 300, concurrency: 2 })
check(r2.status === 200, 'second run started')
await until(async () => (await call('/state')).body.runs.find(x => x.runId === 'smoke2')?.status === 'running', 15000)
check((await call('/runs/smoke2', 'DELETE')).status === 409, 'delete refused while the run is running')
check((await fetch(`${base}/runs/smoke2/zip?kind=full`)).status === 409, 'zip refused while the run is running')
await until(async () => (await call('/state')).body.runs.find(x => x.runId === 'smoke2')?.status === 'complete')
const del = await call('/runs/smoke2', 'DELETE')
check(del.status === 200, 'finished run deleted')
const afterDel = (await call('/state')).body
check(!afterDel.runs.some(x => x.runId === 'smoke2') && afterDel.runs.some(x => x.runId === 'smoke'), 'deleted run gone, other run kept')
check((await call('/files/smoke2/report.html')).status === 404, 'deleted run files gone')
check((await call('/runs/smoke2', 'DELETE')).status === 404, 'deleting again: 404')
check((await call('/runs/summary', 'DELETE')).status === 404, 'summary folder cannot be deleted as a run')
console.log(`runs dir: ${env.SAVINGS_RUNS_DIR}`)
stop(0)
