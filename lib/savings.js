// API for the cache savings test page. Each action runs the Python CLI in
// savings/ (python -m savings ...). Runs are detached and log to their run
// directory, so a run of several hours survives a restart of this server;
// the page reads progress.json and the pid file to show where it is.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, openSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import express from 'express'

const ROOT = new URL('../savings/', import.meta.url).pathname
const PYTHON = process.env.SAVINGS_PYTHON || join(ROOT, '.venv/bin/python')
const RUNS = process.env.SAVINGS_RUNS_DIR || join(ROOT, 'runs')
const DATA = process.env.SAVINGS_DATA_DIR || join(ROOT, 'data')
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$/
const FILES = new Set(['report.html', 'report.md', 'report.json', 'requests.csv', 'meta.json', 'job.log'])
const SUMMARY_FILES = new Set(['summary.html', 'summary.md', 'summary.json'])

const readJson = path => {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    return null
  }
}

const alive = pid => {
  if (!pid) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

// Jobs by key (datasets, run, judge, report, summary), so the page can show
// them. Saved next to the runs, so their last state survives a restart; a job
// that was running when the server stopped is shown as interrupted unless its
// process is still alive (runs are detached and keep going).
const JOBS_FILE = join(RUNS, 'jobs.json')
const jobs = new Map()
for (const job of readJson(JOBS_FILE) ?? []) {
  if (job.state === 'running' && !alive(job.pid)) job.state = 'interrupted'
  jobs.set(job.key, job)
}
const saveJobs = () => {
  mkdirSync(RUNS, { recursive: true })
  writeFileSync(JOBS_FILE + '.tmp', JSON.stringify([...jobs.values()], null, 2))
  renameSync(JOBS_FILE + '.tmp', JOBS_FILE)
}

export function savingsRouter(config) {
  const router = express.Router()

  const childEnv = () => ({
    ...process.env,
    GATEWAY_URL: config.gatewayUrl,
    GATEWAY_API_KEY: config.apiKey,
    INSECURE_TLS: String(config.insecureTls),
    EMBED_MODEL: config.embedModel,
    CHAT_MODEL: config.chatModel,
    SONNET_MODEL: config.sonnetModel,
    JUDGE_MODEL: config.judgeModel,
    SAVINGS_RUNS_DIR: RUNS,
    SAVINGS_DATA_DIR: DATA,
    PYTHONUNBUFFERED: '1',
  })

  function start(key, args, logFile, { detached = false } = {}) {
    const running = jobs.get(key)
    if (running && running.state === 'running' && alive(running.pid)) throw new Error(`${key} is already running`)
    mkdirSync(join(logFile, '..'), { recursive: true })
    const fd = openSync(logFile, 'a')
    writeFileSync(fd, `\n$ python -m savings ${args.join(' ')}  # ${new Date().toISOString()}\n`)
    const child = spawn(PYTHON, ['-m', 'savings', ...args], {
      cwd: ROOT,
      env: childEnv(),
      stdio: ['ignore', fd, fd],
      detached,
    })
    const job = { key, args, state: 'running', pid: child.pid, started: new Date().toISOString(), log: logFile }
    jobs.set(key, job)
    saveJobs()
    const finish = patch => {
      Object.assign(job, patch, { finished: new Date().toISOString() })
      saveJobs()
    }
    child.on('exit', code => finish({ state: code === 0 ? 'done' : 'failed', code }))
    child.on('error', err => finish({ state: 'failed', error: err.message }))
    if (detached) child.unref()
    return job
  }

  const runDir = id => join(RUNS, id)
  const checkId = (req, res, next) => (ID.test(req.params.id) ? next() : res.status(400).json({ error: 'bad run id' }))
  const checkRun = (req, res, next) =>
    existsSync(join(runDir(req.params.id), 'meta.json')) ? next() : res.status(404).json({ error: 'no such run' })
  const tail = (path, lines = 40) =>
    existsSync(path) ? readFileSync(path, 'utf8').split('\n').slice(-lines).join('\n') : ''

  function runInfo(id) {
    const dir = runDir(id)
    const meta = readJson(join(dir, 'meta.json'))
    if (!meta) return null
    const progress = readJson(join(dir, 'progress.json'))
    const pidFile = readJson(join(dir, 'job.json'))
    const running = alive(pidFile?.pid) && progress?.state !== 'complete'
    const report = readJson(join(dir, 'report.json'))
    return {
      runId: id,
      workload: meta.workload,
      model: meta.model,
      limit: meta.limit,
      createdAt: meta.created_at,
      status: running ? 'running' : progress?.state ?? meta.status ?? 'created',
      progress,
      appliedThreshold: meta.applied_threshold,
      appliedExpiry: meta.applied_expiry,
      hasReport: existsSync(join(dir, 'report.html')),
      reportAt: report?.generated_at ?? null,
      wrongHits: report?.wrong_hits ? { wrong: report.wrong_hits.wrong, sampled: report.wrong_hits.sampled } : null,
      judged: existsSync(join(dir, 'judge.jsonl')),
      jobs: ['judge', 'report'].map(k => jobs.get(`${k}:${id}`)).filter(Boolean),
    }
  }

  router.get('/state', (_req, res) => {
    const datasets = {}
    for (const w of ['chatbot', 'rag']) {
      datasets[w] = {
        meta: readJson(join(DATA, `${w}-meta.json`)),
        job: jobs.get(`datasets:${w}`) ?? null,
        log: jobs.get(`datasets:${w}`) ? tail(join(DATA, `${w}-build.log`), 6) : '',
      }
    }
    const ids = existsSync(RUNS)
      ? readdirSync(RUNS, { withFileTypes: true }).filter(d => d.isDirectory() && d.name !== 'summary').map(d => d.name)
      : []
    const runs = ids.map(runInfo).filter(Boolean).sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''))
    res.json({
      python: existsSync(PYTHON),
      models: { sonnet: config.sonnetModel, haiku: config.chatModel, judge: config.judgeModel, embed: config.embedModel },
      gateway: config.gatewayUrl,
      apiKeySet: Boolean(config.apiKey),
      datasets,
      runs,
      summary: { job: jobs.get('summary') ?? null, exists: existsSync(join(RUNS, 'summary', 'summary.html')) },
    })
  })

  router.post('/datasets/:workload', (req, res) => {
    const w = req.params.workload
    if (!['chatbot', 'rag'].includes(w)) return res.status(400).json({ error: 'workload is chatbot or rag' })
    try {
      res.json(start(`datasets:${w}`, ['datasets', '--workload', w], join(DATA, `${w}-build.log`)))
    } catch (err) {
      res.status(409).json({ error: err.message })
    }
  })

  function startRun(id, args) {
    const dir = runDir(id)
    mkdirSync(dir, { recursive: true })
    const prev = readJson(join(dir, 'job.json'))
    if (alive(prev?.pid)) throw new Error(`run ${id} is already running (pid ${prev.pid})`)
    const job = start(`run:${id}`, args, join(dir, 'job.log'), { detached: true })
    writeFileSync(join(dir, 'job.json'), JSON.stringify({ pid: job.pid, args, started: job.started }))
    return job
  }

  router.post('/runs', (req, res) => {
    const b = req.body ?? {}
    const workload = b.workload
    const model = String(b.model ?? '').trim()
    if (!['chatbot', 'rag'].includes(workload)) return res.status(400).json({ error: 'workload is chatbot or rag' })
    if (!model || /\s/.test(model)) return res.status(400).json({ error: 'model is required' })
    const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 13).replace('T', '-')
    const id = String(b.runId || `${workload}-${model.replace(/[^A-Za-z0-9.-]/g, '')}-${stamp}`).trim()
    if (!ID.test(id)) return res.status(400).json({ error: 'run id: letters, digits, . _ - only' })
    if (['meta.json', 'job.json'].some(f => existsSync(join(runDir(id), f)))) return res.status(409).json({ error: `run ${id} exists; resume it instead` })
    const int = (v, lo, hi, name) => {
      if (v === undefined || v === null || v === '') return null
      const n = Number(v)
      if (!Number.isInteger(n) || n < lo || n > hi) throw new Error(`${name}: whole number ${lo}–${hi}`)
      return n
    }
    try {
      const limit = int(b.limit, 1, 1000000, 'limit')
      const concurrency = int(b.concurrency, 1, 64, 'concurrency') ?? 8
      const expiry = int(b.expiry, 60, 30 * 86400, 'expiry') ?? 172800
      const args = ['run', '--workload', workload, '--model', model, '--run-id', id,
        '--concurrency', String(concurrency), '--expiry', String(expiry)]
      if (limit) args.push('--limit', String(limit))
      startRun(id, args)
      res.json(runInfo(id) ?? { runId: id, status: 'starting' })
    } catch (err) {
      res.status(400).json({ error: err.message })
    }
  })

  router.post('/runs/:id/resume', checkId, (req, res) => {
    const id = req.params.id
    const meta = readJson(join(runDir(id), 'meta.json'))
    if (!meta) return res.status(404).json({ error: 'no such run' })
    const prev = readJson(join(runDir(id), 'job.json'))
    const args = prev?.args ?? ['run', '--workload', meta.workload, '--model', meta.model, '--run-id', id]
    try {
      startRun(id, args)
      res.json(runInfo(id))
    } catch (err) {
      res.status(409).json({ error: err.message })
    }
  })

  router.post('/runs/:id/stop', checkId, checkRun, (req, res) => {
    const pid = readJson(join(runDir(req.params.id), 'job.json'))?.pid
    if (!alive(pid)) return res.status(409).json({ error: 'not running' })
    // SIGTERM: the runner stops taking new requests and finishes those in flight.
    process.kill(pid, 'SIGTERM')
    res.json({ ok: true })
  })

  router.post('/runs/:id/judge', checkId, checkRun, (req, res) => {
    const id = req.params.id
    const sample = Number(req.body?.sample ?? 100)
    if (!Number.isInteger(sample) || sample < 1 || sample > 1000) return res.status(400).json({ error: 'sample: 1–1000' })
    try {
      res.json(start(`judge:${id}`, ['judge', '--run', id, '--sample', String(sample)], join(runDir(id), 'job.log')))
    } catch (err) {
      res.status(409).json({ error: err.message })
    }
  })

  router.post('/runs/:id/report', checkId, checkRun, (req, res) => {
    const id = req.params.id
    try {
      res.json(start(`report:${id}`, ['report', '--run', id], join(runDir(id), 'job.log')))
    } catch (err) {
      res.status(409).json({ error: err.message })
    }
  })

  router.get('/runs/:id/log', checkId, (req, res) => res.type('text/plain').send(tail(join(runDir(req.params.id), 'job.log'), 80)))

  // Judged semantic hits with the answer that was served, for the human review.
  router.get('/runs/:id/review', checkId, (req, res) => {
    const dir = runDir(req.params.id)
    const path = join(dir, 'judge.jsonl')
    if (!existsSync(path)) return res.json({ items: [] })
    const items = readFileSync(path, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
    const want = new Set(items.map(i => i.seq))
    const answers = {}
    const apath = join(dir, 'answers.jsonl')
    if (existsSync(apath)) {
      for (const line of readFileSync(apath, 'utf8').split('\n')) {
        if (!line) continue
        const a = JSON.parse(line)
        if (want.has(a.seq)) answers[a.seq] = a.answer
      }
    }
    const review = readJson(join(dir, 'review.json')) ?? {}
    res.json({ items: items.map(i => ({ ...i, answer: answers[i.seq] ?? '', human: review[i.seq] ?? null })) })
  })

  router.put('/runs/:id/review', checkId, (req, res) => {
    const dir = runDir(req.params.id)
    if (!existsSync(join(dir, 'judge.jsonl'))) return res.status(404).json({ error: 'not judged' })
    const { seq, verdict } = req.body ?? {}
    if (!Number.isInteger(seq) || ![null, 'fit', 'wrong', 'unsure'].includes(verdict ?? null)) {
      return res.status(400).json({ error: 'seq (integer) and verdict fit|wrong|unsure|null' })
    }
    const path = join(dir, 'review.json')
    const review = readJson(path) ?? {}
    if (verdict) review[seq] = verdict
    else delete review[seq]
    writeFileSync(path, JSON.stringify(review, null, 2))
    res.json({ ok: true, reviewed: Object.keys(review).length })
  })

  router.post('/summary', (req, res) => {
    const ids = (req.body?.runs ?? []).map(String)
    if (!ids.length || !ids.every(i => ID.test(i))) return res.status(400).json({ error: 'pick at least one run' })
    try {
      res.json(start('summary', ['summary', '--runs', ids.join(',')], join(RUNS, 'summary', 'job.log')))
    } catch (err) {
      res.status(409).json({ error: err.message })
    }
  })

  router.get('/files/summary/:file', (req, res) => {
    if (!SUMMARY_FILES.has(req.params.file)) return res.status(404).end()
    const path = join(RUNS, 'summary', req.params.file)
    return existsSync(path) ? res.sendFile(path) : res.status(404).end()
  })

  router.get('/files/:id/:file', checkId, (req, res) => {
    if (!FILES.has(req.params.file)) return res.status(404).end()
    const path = join(runDir(req.params.id), req.params.file)
    if (!existsSync(path)) return res.status(404).end()
    if (req.params.file === 'requests.csv') res.attachment(`${req.params.id}-requests.csv`)
    return res.sendFile(path)
  })

  return router
}
