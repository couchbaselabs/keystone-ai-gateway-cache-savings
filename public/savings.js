const $ = (sel, el = document) => el.querySelector(sel)
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)]

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag)
  Object.assign(node, attrs)
  for (const c of children) if (c !== null && c !== undefined) node.append(c)
  return node
}

const fmt = (n, d = 0) => (n === null || n === undefined ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }))
const pct = v => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`)
const dur = s => {
  if (s === null || s === undefined) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.round((s % 3600) / 60)
  return h ? `${h}h ${m}m` : `${m}m`
}

const WORKLOADS = { chatbot: 'A. Chatbot / FAQ', rag: 'B. Agentic / RAG' }
// List prices per million tokens (input, output) from the test request, and the
// midpoint of each workload's expected output length, for the upper-bound estimate.
const PRICES = { sonnet: [3, 15], haiku: [1, 5] }
const OUT_EST = { chatbot: 225, rag: 400 }

const state = { data: null, selected: null, tab: 'report', checked: new Set(), reportAt: null }

// Per-browser view state (selected run, tab, summary picks, run form), so a
// reload returns to the same place. Results themselves live on the server.
const VIEW_KEY = 'savings-view-v1'
function saveView() {
  try {
    const f = $('#run-form')
    const form = Object.fromEntries(['workload', 'model', 'limit', 'concurrency', 'expiry'].map(k => [k, f[k].value]))
    localStorage.setItem(VIEW_KEY, JSON.stringify({ selected: state.selected, tab: state.tab, checked: [...state.checked], form }))
  } catch {
    // storage unavailable (private mode): the page still works, it just forgets
  }
}
function loadView() {
  try {
    const v = JSON.parse(localStorage.getItem(VIEW_KEY) ?? 'null')
    if (!v) return
    state.selected = v.selected ?? null
    state.tab = ['report', 'review', 'log'].includes(v.tab) ? v.tab : 'report'
    state.checked = new Set(v.checked ?? [])
    const f = $('#run-form')
    for (const [k, val] of Object.entries(v.form ?? {})) if (f[k] && val !== undefined) f[k].value = val
  } catch {
    // ignore a corrupt or unavailable store
  }
}

async function api(path, opts = {}) {
  const res = await fetch(`api/savings${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  const ct = res.headers.get('content-type') ?? ''
  const body = ct.includes('json') ? await res.json() : await res.text()
  if (!res.ok) throw new Error(body?.error ?? `${res.status}`)
  return body
}

// ---------------------------------------------------------------- header ---
function renderConfig() {
  const d = state.data
  const items = [
    ['Gateway', d.gateway],
    ['API key', d.apiKeySet ? 'set' : 'not set'],
    ['Sonnet', d.models.sonnet],
    ['Haiku', d.models.haiku],
    ['Embedding', d.models.embed],
    ['Judge', d.models.judge],
  ]
  $('#config').replaceChildren(
    ...items.map(([k, v]) => el('span', {}, el('b', { textContent: `${k}: ` }), el('span', { className: k === 'API key' && !d.apiKeySet ? 'bad' : '', textContent: v }))),
  )
  const warn = []
  if (!d.python) warn.push('Python environment missing: run `npm run savings:setup`.')
  if (!d.apiKeySet) warn.push('Set the gateway API key in Preferences.')
  Object.assign($('#warn'), { hidden: !warn.length, textContent: warn.join(' ') })
}

// -------------------------------------------------------------- datasets ---
function renderDatasets() {
  const boxes = Object.entries(state.data.datasets).map(([w, ds]) => {
    const m = ds.meta
    const running = ds.job?.state === 'running'
    const btn = el('button', { className: 'small', textContent: running ? 'Building…' : m ? 'Rebuild' : 'Build', disabled: running })
    btn.addEventListener('click', async () => {
      if (m && !confirm(`Rebuild the ${w} stream? Existing runs keep their results, but new runs will use the new stream.`)) return
      try {
        await api(`/datasets/${w}`, { method: 'POST' })
      } catch (err) {
        alert(err.message)
      }
      refresh()
    })
    const rows = m
      ? [
          ['Source', m.source],
          ['Requests', fmt(m.stats.requests)],
          ['Distinct messages', fmt(m.stats.distinct_messages)],
          ['Exact repeats', pct(m.stats.exact_repeat_share)],
          ['System prompt', `≈${fmt(m.system_tokens_est)} tokens`],
          ['User message', `≈${fmt(m.stats.user_tokens_est_mean)} tokens`],
          ['Max output', `${fmt(m.max_tokens)} tokens`],
        ]
      : [['Status', 'not built']]
    if (w === 'rag' && !m) rows.push(['Note', 'Fetches Capella docs and writes questions with Haiku through the gateway (about $2).'])
    const box = el(
      'div',
      { className: 'ds' },
      el('h3', {}, el('span', { textContent: WORKLOADS[w] }), btn),
      el('dl', {}, ...rows.flatMap(([k, v]) => [el('dt', { textContent: k }), el('dd', { textContent: v })])),
    )
    if (['failed', 'interrupted'].includes(ds.job?.state)) box.append(el('p', { className: 'field-error', textContent: `Last build ${ds.job.state}:` }))
    if (ds.log) box.append(el('pre', { textContent: ds.log }))
    return box
  })
  $('#datasets').replaceChildren(...boxes)
  updateEstimate()
}

function updateEstimate() {
  const f = $('#run-form')
  const w = f.workload.value
  const model = f.model.value
  const m = state.data?.datasets[w]?.meta
  if (!m) {
    $('#estimate').textContent = `Build the ${WORKLOADS[w]} dataset first.`
    return
  }
  const n = Math.min(Number(f.limit.value) || m.stats.requests, m.stats.requests)
  const [pin, pout] = PRICES[model]
  const inTok = m.system_tokens_est + m.stats.user_tokens_est_mean
  const usd = (n * (inTok * pin + OUT_EST[w] * pout)) / 1e6
  $('#estimate').textContent = `${fmt(n)} requests · ≈${fmt(inTok)} input tokens each · upper bound if every request misses ≈ $${fmt(usd)}`
}

// ------------------------------------------------------------------ runs ---
function renderRuns() {
  const runs = state.data.runs
  $('#no-runs').hidden = runs.length > 0
  $('#runs').replaceChildren(
    ...runs.map(r => {
      const p = r.progress ?? {}
      const total = p.total ?? r.limit ?? 20000
      const done = p.done ?? 0
      const check = el('input', { type: 'checkbox', checked: state.checked.has(r.runId), ariaLabel: `select ${r.runId}` })
      check.addEventListener('click', e => {
        e.stopPropagation()
        check.checked ? state.checked.add(r.runId) : state.checked.delete(r.runId)
        saveView()
        $('#summary-btn').disabled = state.checked.size === 0
      })
      const tr = el(
        'tr',
        { className: state.selected === r.runId ? 'sel' : '' },
        el('td', {}, check),
        el('td', {}, el('span', { className: 'rid', textContent: r.runId }), el('span', { className: 'sub2', textContent: `${WORKLOADS[r.workload]} · ${r.model}` })),
        el('td', {}, el('span', { className: `pill ${r.status}`, textContent: r.status.replace(/_/g, ' ') })),
        el(
          'td',
          {},
          el('span', { textContent: `${fmt(done)} / ${fmt(total)}` }),
          el('span', { className: 'sub2', textContent: r.status === 'running' ? `${fmt(p.req_per_s, 2)} req/s · ETA ${dur(p.eta_s)}${p.errors ? ` · ${p.errors} errors` : ''}` : p.errors ? `${p.errors} errors` : '' }),
          el('div', { className: 'bar' }, el('i', { style: `width:${total ? (100 * done) / total : 0}%` })),
        ),
        el('td', {}, el('span', { textContent: pct(p.hit_rate) }), el('span', { className: 'sub2', textContent: p.done ? `std ${fmt(p.standard)} · sem ${fmt(p.semantic)}` : '' })),
        el('td', { textContent: r.wrongHits ? `${r.wrongHits.wrong} / ${r.wrongHits.sampled}` : r.judged ? 'judged' : '—' }),
        el('td', {}, el('button', { className: 'small', textContent: 'Open' })),
      )
      tr.addEventListener('click', () => select(r.runId))
      return tr
    }),
  )
  $('#summary-btn').disabled = state.checked.size === 0
  $('#summary-link').hidden = !state.data.summary.exists
  const sj = state.data.summary.job
  $('#summary-btn').textContent = sj?.state === 'running' ? 'Building summary…' : 'Build summary of selected'
}

function select(id) {
  state.selected = id
  state.reportAt = null
  saveView()
  renderRuns()
  renderDetail(true)
}

function actionButton(label, fn, { primary = false, danger = false, disabled = false } = {}) {
  const b = el('button', { className: `small ${primary ? 'primary' : ''} ${danger ? 'danger' : ''}`, textContent: label, disabled })
  b.addEventListener('click', async () => {
    b.disabled = true
    try {
      await fn()
    } catch (err) {
      alert(err.message)
    }
    refresh()
  })
  return b
}

function renderDetail(force = false) {
  const r = state.data.runs.find(x => x.runId === state.selected)
  $('#detail').hidden = !r
  if (!r) return
  $('#detail-title').textContent = r.runId
  const running = r.status === 'running'
  const busy = j => r.jobs.some(x => x.key.startsWith(j) && x.state === 'running')
  const actions = [
    running
      ? actionButton('Stop', () => api(`/runs/${r.runId}/stop`, { method: 'POST' }))
      : actionButton('Resume', () => api(`/runs/${r.runId}/resume`, { method: 'POST' }), { disabled: r.status === 'complete' }),
    actionButton(busy('judge') ? 'Judging…' : 'Judge 100 semantic hits', () => api(`/runs/${r.runId}/judge`, { method: 'POST', body: { sample: 100 } }), { disabled: running || busy('judge') }),
    actionButton(busy('report') ? 'Rebuilding…' : 'Rebuild report', () => api(`/runs/${r.runId}/report`, { method: 'POST' }), { disabled: busy('report') }),
    el('a', { className: 'button', href: `api/savings/files/${r.runId}/report.html`, target: '_blank', textContent: 'Report ↗' }),
    el('a', { className: 'button', href: `api/savings/files/${r.runId}/requests.csv`, textContent: 'CSV' }),
    actionButton('Delete run', () => deleteRun(r), { danger: true, disabled: running || busy('judge') || busy('report') }),
  ]
  $('#detail-actions').replaceChildren(...actions)
  if (state.tab === 'report' && (force || r.reportAt !== state.reportAt)) {
    state.reportAt = r.reportAt
    $('#report-frame').src = r.hasReport ? `api/savings/files/${r.runId}/report.html?t=${encodeURIComponent(r.reportAt ?? '')}` : 'about:blank'
  }
  if (state.tab === 'log') loadLog()
  if (state.tab === 'review' && force) loadReview()
}

async function deleteRun(r) {
  const p = r.progress ?? {}
  const what = `${r.runId}\n${WORKLOADS[r.workload]} · ${r.model} · ${fmt(p.done ?? 0)} requests${r.judged ? ' · judged' : ''}`
  if (!confirm(`Delete this run and all its files (CSV, report, wrong-hit reviews)? This cannot be undone.\n\n${what}`)) return
  await api(`/runs/${r.runId}`, { method: 'DELETE' })
  state.checked.delete(r.runId)
  if (state.selected === r.runId) state.selected = null
  saveView()
}

async function loadLog() {
  $('#log').textContent = await api(`/runs/${state.selected}/log`)
}

// ---------------------------------------------------------------- review ---
async function loadReview() {
  const id = state.selected
  const { items } = await api(`/runs/${id}/review`)
  if (!items.length) {
    $('#review').replaceChildren(el('p', { className: 'meta', textContent: 'Not judged yet. Use "Judge 100 semantic hits" once the run has semantic hits.' }))
    return
  }
  const final = i => i.human ?? i.verdict
  const wrong = items.filter(i => final(i) === 'wrong').length
  const head = el(
    'div',
    { className: 'review-head' },
    el('span', { textContent: `${wrong} of ${items.length} judged wrong · ${items.filter(i => i.human).length} reviewed by you. Your verdict replaces the judge's in the report after "Rebuild report".` }),
    el('label', {}, el('input', { type: 'checkbox', id: 'only-wrong' }), ' only wrong / unsure'),
  )
  const cards = items.map(i => {
    const v = final(i)
    const card = el(
      'div',
      { className: 'rv', dataset: { verdict: v } },
      el('p', { className: 'q', textContent: `#${i.seq} · ${i.question}` }),
      el('p', { className: 'mq', textContent: `Cached answer was written for: ${i.matched_question} (score ${i.score ?? '—'})` }),
      el('p', { className: 'reason' }, el('span', { className: `pill ${i.verdict}`, textContent: `judge: ${i.verdict}` }), ` ${i.reason ?? ''}`),
      el('details', {}, el('summary', { textContent: 'Answer served' }), el('pre', { textContent: i.answer })),
    )
    const row = el('div', { className: 'verdicts' }, el('span', { textContent: 'Your verdict:' }))
    for (const verdict of ['fit', 'wrong', 'unsure', null]) {
      const b = el('button', { className: 'small', textContent: verdict ?? 'clear', ariaPressed: String(i.human === verdict && verdict !== null) })
      b.addEventListener('click', async () => {
        await api(`/runs/${id}/review`, { method: 'PUT', body: { seq: i.seq, verdict } })
        loadReview()
      })
      row.append(b)
    }
    card.append(row)
    return card
  })
  $('#review').replaceChildren(head, ...cards)
  $('#only-wrong').addEventListener('change', e => {
    for (const c of $$('.rv')) c.hidden = e.target.checked && c.dataset.verdict === 'fit'
  })
}

// ------------------------------------------------------------------ main ---
async function refresh() {
  try {
    state.data = await api('/state')
  } catch (err) {
    Object.assign($('#warn'), { hidden: false, textContent: `Cannot load state: ${err.message}` })
    return
  }
  const ids = new Set(state.data.runs.map(r => r.runId))
  for (const id of state.checked) if (!ids.has(id)) state.checked.delete(id)
  const first = !state.loaded
  state.loaded = true
  if (state.selected && !ids.has(state.selected)) state.selected = null
  renderConfig()
  renderDatasets()
  renderRuns()
  if (state.selected) renderDetail(first)
}

function main() {
  loadView()
  for (const t of $$('.tabs button')) t.setAttribute('aria-selected', String(t.dataset.tab === state.tab))
  for (const p of $$('[data-panel]')) p.hidden = p.dataset.panel !== state.tab
  $('#run-form').addEventListener('input', () => {
    updateEstimate()
    saveView()
  })
  $('#run-form').addEventListener('submit', async e => {
    e.preventDefault()
    const f = e.target
    const body = Object.fromEntries(new FormData(f))
    const m = state.data?.datasets[body.workload]?.meta
    if (!m) return alert(`Build the ${WORKLOADS[body.workload]} dataset first.`)
    const n = Number(body.limit) || m.stats.requests
    if (!confirm(`Send ${fmt(n)} requests to ${state.data.models[body.model]} through ${state.data.gateway}?\n\n${$('#estimate').textContent}`)) return
    $('#run-error').hidden = true
    try {
      const r = await api('/runs', { method: 'POST', body })
      f.runId.value = ''
      await refresh()
      select(r.runId)
    } catch (err) {
      Object.assign($('#run-error'), { hidden: false, textContent: err.message })
    }
  })
  $('#summary-btn').addEventListener('click', async () => {
    try {
      await api('/summary', { method: 'POST', body: { runs: [...state.checked] } })
    } catch (err) {
      alert(err.message)
    }
    refresh()
  })
  for (const b of $$('.tabs button')) {
    b.addEventListener('click', () => {
      state.tab = b.dataset.tab
      saveView()
      for (const t of $$('.tabs button')) t.setAttribute('aria-selected', String(t === b))
      for (const p of $$('[data-panel]')) p.hidden = p.dataset.panel !== state.tab
      renderDetail(true)
    })
  }
  refresh()
  setInterval(refresh, 3000)
}

main()
