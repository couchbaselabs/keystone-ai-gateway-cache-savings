import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { spawn } from 'node:child_process'
import express from 'express'
import { config, publicConfig, updatePreferences } from './lib/config.js'
import { claimList, runClaim } from './lib/claims.js'
import { savingsRouter } from './lib/savings.js'

const app = express()
app.use(express.json())
app.use(express.static(new URL('./public', import.meta.url).pathname))

app.get('/api/config', (_req, res) => res.json(publicConfig()))
app.use('/api/savings', savingsRouter(config))
app.get('/api/claims', (_req, res) => res.json(claimList()))

// Latest result per claim, saved to disk so a page reload or a server restart
// keeps the summary table.
const resultsFile = process.env.RESULTS_FILE || new URL('./results/claim-results.json', import.meta.url).pathname
const loadResults = () => {
  try {
    return existsSync(resultsFile) ? JSON.parse(readFileSync(resultsFile, 'utf8')) : {}
  } catch (err) {
    console.warn(`Ignoring unreadable ${resultsFile}: ${err.message}`)
    return {}
  }
}
const saveResults = () => {
  mkdirSync(dirname(resultsFile), { recursive: true })
  writeFileSync(resultsFile + '.tmp', JSON.stringify(results, null, 2) + '\n')
  renameSync(resultsFile + '.tmp', resultsFile)
}
const results = loadResults()
// Drop saved results of claims that were removed from the list.
{
  const known = new Set(claimList().map(c => c.id))
  for (const k of Object.keys(results)) if (!known.has(k)) delete results[k]
}
app.get('/api/results', (_req, res) => res.json(results))

// Run one claim at a time: claims read shared metrics counters and the
// shared cache, so overlapping runs would muddy each other's evidence.
// Preference changes wait in the same queue so they never land mid-run.
let queue = Promise.resolve()
const enqueue = fn => {
  const job = queue.then(fn)
  queue = job.catch(() => {})
  return job
}

app.post('/api/claims/:id/run', (req, res) => {
  enqueue(() => runClaim(req.params.id, config))
    .then(r => {
      results[req.params.id] = { ...r, at: new Date().toISOString() }
      saveResults()
      res.json(results[req.params.id])
    })
    .catch(err => res.status(500).json({ status: 'ERROR', summary: err.message }))
})

app.put('/api/preferences', (req, res) => {
  enqueue(() => updatePreferences(req.body ?? {}))
    .then(r => {
      if (!r.ok) return res.status(400).json(r)
      // Old results describe the old settings; keep them only if nothing changed.
      if (r.changed.length) {
        for (const k of Object.keys(results)) delete results[k]
        saveResults()
      }
      res.json({ ...r, config: publicConfig() })
    })
    .catch(err => res.status(500).json({ ok: false, errors: { _: err.message } }))
})

// Listen on PORT, or the next free port when it is taken (for example by an
// older copy of this server), trying up to 20 ports. PORT_FALLBACK=false turns
// this off, so tests that expect an exact port fail instead of moving.
const MAX_PORT_TRIES = process.env.PORT_FALLBACK === 'false' ? 0 : 20
function listen(port, triesLeft) {
  const server = app.listen(port, config.host)
  server.once('listening', () => {
    if (port !== config.port) console.log(`Port ${config.port} is busy; using ${port} instead.`)
    config.port = port
    console.log(`Serverless Model Service - Cache Savings Test Runs on http://${config.host}:${port}`)
    console.log(`Gateway: ${config.gatewayUrl}  chat: ${config.chatModel}  embed: ${config.embedModel}`)
    if (!config.apiKey) console.log('GATEWAY_API_KEY is not set; set it in Preferences or .env.')
    openBrowser(`http://${config.host}:${port}/savings.html`)
  })
  server.once('error', err => {
    if (err.code === 'EADDRINUSE' && triesLeft > 0) return listen(port + 1, triesLeft - 1)
    console.error(`Cannot listen on ${config.host}:${port}: ${err.message}`)
    process.exit(1)
  })
}
listen(config.port, MAX_PORT_TRIES)

// Open the page in the default browser once the server is up. OPEN_BROWSER=false
// (or a CI environment) skips it; a missing opener only prints the URL.
function openBrowser(url) {
  if (process.env.OPEN_BROWSER === 'false' || process.env.CI) return
  const [cmd, args] =
    process.platform === 'darwin' ? ['open', [url]]
      : process.platform === 'win32' ? ['cmd', ['/c', 'start', '', url]]
        : ['xdg-open', [url]]
  try {
    const child = spawn(cmd, args, { stdio: 'ignore', detached: true })
    child.on('error', () => console.log(`Open ${url} in your browser.`))
    child.unref()
  } catch {
    console.log(`Open ${url} in your browser.`)
  }
}
