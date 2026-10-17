const $ = (sel, el = document) => el.querySelector(sel)
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)]

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag)
  Object.assign(node, attrs)
  for (const c of children) node.append(c)
  return node
}

const STATUSES = ['CONFIRMED', 'REFUTED', 'INCONCLUSIVE', 'SKIPPED', 'ERROR']
const state = { claims: [], results: {}, running: new Set(), config: null }

// ---------------------------------------------------------------- config ---
function renderConfigStrip() {
  const byKey = Object.fromEntries(state.config.prefs.map(p => [p.key, p]))
  const val = k => byKey[k]?.value || '—'
  const set = k => (byKey[k]?.isSet ? 'set' : 'not set')
  const items = [
    ['Gateway', val('GATEWAY_URL')],
    ['API key', set('GATEWAY_API_KEY')],
    ['Chat model', val('CHAT_MODEL')],
    ['Embedding model', val('EMBED_MODEL')],
  ]
  const missingKey = !byKey.GATEWAY_API_KEY?.isSet
  $('#config').replaceChildren(
    ...items.map(([k, v]) =>
      el('span', {}, el('b', { textContent: k + ': ' }), el('span', { className: k === 'API key' && missingKey ? 'bad' : '', textContent: v })),
    ),
  )
}

async function loadConfig() {
  state.config = await (await fetch('/api/config')).json()
  renderConfigStrip()
}

// --------------------------------------------------------------- summary ---
function statusText(id) {
  if (state.running.has(id)) return ['running', 'running…']
  const r = state.results[id]
  return r ? [r.status, r.status] : ['idle', 'not run']
}

function renderSummary() {
  const rows = state.claims.map(c => {
    const r = state.results[c.id]
    const [cls, text] = statusText(c.id)
    const runBtn = el('button', { className: 'run small', textContent: 'Run', disabled: state.running.has(c.id) })
    runBtn.addEventListener('click', e => {
      e.stopPropagation()
      runClaim(c.id)
    })
    const row = el(
      'tr',
      {},
      el('td', { className: 'n', textContent: c.n }),
      el('td', { className: 'title', textContent: c.title }),
      el('td', {}, el('span', { className: `pill ${cls}`, textContent: text })),
      el('td', { className: 'res' }, el('div', { textContent: r?.summary ?? '', title: r?.summary ?? '' })),
      el('td', { className: 'time', textContent: r?.ms ? `${(r.ms / 1000).toFixed(1)} s` : '' }),
      el('td', {}, runBtn),
    )
    row.addEventListener('click', () => document.getElementById(`claim-${c.id}`).scrollIntoView({ behavior: 'smooth' }))
    return row
  })
  $('#summary').replaceChildren(...rows)

  const counts = Object.fromEntries(STATUSES.map(s => [s, 0]))
  for (const r of Object.values(state.results)) counts[r.status] = (counts[r.status] ?? 0) + 1
  const run = Object.values(state.results).length
  $('#counts').replaceChildren(
    el('span', { textContent: `${run} of ${state.claims.length} run` }),
    ...STATUSES.filter(s => counts[s]).map(s => el('span', { className: `pill ${s}`, textContent: `${counts[s]} ${s.toLowerCase()}` })),
  )
}

// ----------------------------------------------------------------- cards ---
function renderStep(s) {
  const title = [
    s.label,
    s.cache && `X-Cache ${s.cache}`,
    s.score !== null && s.score !== undefined && `score ${s.score}`,
    s.ms !== undefined && `${s.ms} ms`,
  ]
    .filter(Boolean)
    .join(' · ')
  const detail = {
    path: s.path,
    status: s.status,
    requestHeaders: s.requestHeaders,
    requestBody: s.requestBody,
    responseHeaders: s.responseHeaders,
    usage: s.usage,
    answer: s.answer,
  }
  for (const k of Object.keys(detail)) if (detail[k] === undefined || detail[k] === null) delete detail[k]
  return el('div', { className: 'step' }, el('h3', { textContent: title }), el('pre', { textContent: JSON.stringify(detail, null, 2) }))
}

function renderCard(c) {
  const card = document.getElementById(`claim-${c.id}`)
  const r = state.results[c.id]
  const [cls, text] = statusText(c.id)
  const pill = $('.pill', card)
  pill.className = `pill ${cls}`
  pill.textContent = text + (r?.ms && !state.running.has(c.id) ? ` · ${(r.ms / 1000).toFixed(1)} s` : '')
  $('.run', card).disabled = state.running.has(c.id)
  const result = $('.result', card)
  result.hidden = !r
  if (!r) return
  $('.result-summary', result).textContent = r.summary
  $('.evidence', result).replaceChildren(
    ...(r.evidence ?? []).map(([k, v]) => el('tr', {}, el('td', { textContent: k }), el('td', { textContent: v }))),
  )
  $('.steps', result).replaceChildren(...(r.steps ?? []).map(renderStep))
}

function render(id) {
  renderSummary()
  for (const c of state.claims) if (!id || c.id === id) renderCard(c)
}

async function runClaim(id) {
  state.running.add(id)
  render(id)
  try {
    const res = await fetch(`/api/claims/${id}/run`, { method: 'POST' })
    state.results[id] = await res.json()
  } catch (err) {
    state.results[id] = { status: 'ERROR', summary: err.message }
  } finally {
    state.running.delete(id)
    render(id)
  }
}

// ----------------------------------------------------------- preferences ---
function openPrefs() {
  const { prefs, envFile } = state.config
  $('#env-file').textContent = envFile
  $('#prefs-error').hidden = true
  const groups = [...new Set(prefs.map(p => p.group))]
  $('#prefs-fields').replaceChildren(
    ...groups.map(g =>
      el(
        'fieldset',
        {},
        el('legend', { textContent: g }),
        ...prefs.filter(p => p.group === g).map(prefField),
      ),
    ),
  )
  $('#prefs').showModal()
}

function prefField(p) {
  const id = `pref-${p.key}`
  let input
  if (p.type === 'bool') {
    input = el('input', { type: 'checkbox', id, name: p.key, checked: p.value === true })
  } else {
    input = el('input', {
      id,
      name: p.key,
      type: p.secret ? 'password' : p.type === 'int' ? 'number' : p.type === 'url' ? 'url' : 'text',
      value: p.secret ? '' : p.value ?? '',
      placeholder: p.secret ? (p.isSet ? 'set: leave blank to keep' : 'not set') : p.optional ? 'optional' : '',
      autocomplete: 'off',
      spellcheck: false,
    })
    if (p.type === 'int') Object.assign(input, { min: p.min, max: p.max, step: 1 })
  }
  const label = el('label', { htmlFor: id, textContent: p.label })
  const wrap = el('div', { className: `field ${p.type === 'bool' ? 'bool' : ''}` }, label, input)
  if (p.secret && p.isSet) {
    const clear = el('label', { className: 'clear' }, el('input', { type: 'checkbox', name: `${p.key}__clear` }), ' clear')
    wrap.append(clear)
  }
  wrap.append(el('span', { className: 'field-error', hidden: true }))
  return wrap
}

async function savePrefs(e) {
  e.preventDefault()
  const form = $('#prefs-form')
  const body = {}
  for (const p of state.config.prefs) {
    const input = form.elements[p.key]
    if (p.type === 'bool') body[p.key] = input.checked ? 'true' : 'false'
    else if (p.secret) body[p.key] = form.elements[`${p.key}__clear`]?.checked ? null : input.value
    else body[p.key] = input.value
  }
  $$('.field-error', form).forEach(s => (s.hidden = true))
  $('#prefs-save').disabled = true
  try {
    const res = await fetch('/api/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const r = await res.json()
    if (!r.ok) {
      for (const [k, msg] of Object.entries(r.errors ?? {})) {
        const span = form.elements[k]?.closest('.field')?.querySelector('.field-error')
        if (span) Object.assign(span, { hidden: false, textContent: msg })
        else Object.assign($('#prefs-error'), { hidden: false, textContent: msg })
      }
      return
    }
    state.config = r.config
    renderConfigStrip()
    if (r.changed.length) {
      state.results = {}
      render()
    }
    $('#prefs').close()
  } catch (err) {
    Object.assign($('#prefs-error'), { hidden: false, textContent: err.message })
  } finally {
    $('#prefs-save').disabled = false
  }
}

// ------------------------------------------------------------------ main ---
async function main() {
  await loadConfig()
  state.claims = await (await fetch('/api/claims')).json()
  state.results = await (await fetch('/api/results')).json()
  const tpl = $('#card')
  for (const c of state.claims) {
    const card = tpl.content.firstElementChild.cloneNode(true)
    card.id = `claim-${c.id}`
    $('.num', card).textContent = `${c.n}.`
    $('.title', card).textContent = c.title
    $('.claim', card).textContent = c.claim
    $('.meta', card).textContent = `Doc steps: ${c.docSteps} · Code: ${c.code}`
    $('.run', card).addEventListener('click', () => runClaim(c.id))
    $('#claims').append(card)
  }
  render()

  $('#run-all').addEventListener('click', async e => {
    e.target.disabled = true
    for (const c of state.claims) await runClaim(c.id)
    e.target.disabled = false
  })
  $('#open-prefs').addEventListener('click', openPrefs)
  $('#prefs-cancel').addEventListener('click', () => $('#prefs').close())
  $('#prefs-form').addEventListener('submit', savePrefs)
  if (location.hash === '#preferences') openPrefs()
}

main()
