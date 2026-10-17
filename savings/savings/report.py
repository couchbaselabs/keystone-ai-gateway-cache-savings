"""Turns a run's requests.csv into the measures the cache-savings test asks for.

For each checkpoint (after 1,000, 10,000 and 20,000 requests, counted
cumulatively from the start of the stream):

1. requests, hits and hit rate, split standard / semantic
2. input and output tokens saved per hit and in total (the usage the gateway
   replays on a hit, i.e. Bedrock's counts from when the answer was cached)
3. embedding tokens the semantic cache consumed
4. latency p50 / p95 on hits and misses
5. wrong hits from the judged sample of semantic hits (judge.py)
6. the raw CSV (requests.csv in the run directory)

Plus a replay of the cache at other similarity thresholds from the logged
embeddings, so no second run at a stricter threshold is needed.
"""

from __future__ import annotations

import csv
import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import RUNS_DIR, TITLE, price_for
from .csvlog import ensure_response_column
from .shadow import VectorLog

CHECKPOINTS = [1000, 10000, 20000]
THRESHOLDS = [round(0.70 + 0.02 * i, 2) for i in range(15)]  # 0.70 ... 0.98

INT_COLS = [
    'seq', 'matched_seq', 'shadow_match_seq', 'prompt_tokens', 'completion_tokens',
    'saved_input_tokens', 'saved_output_tokens', 'billed_input_tokens',
    'billed_output_tokens', 'embed_tokens', 'attempts', 'http_status',
]
FLOAT_COLS = ['gateway_score', 'shadow_score', 'latency_ms', 'embed_latency_ms']


def _typed(r: dict) -> dict:
    out = dict(r)
    for c in INT_COLS:
        out[c] = int(float(r[c])) if r.get(c) not in (None, '') else None
    for c in FLOAT_COLS:
        out[c] = float(r[c]) if r.get(c) not in (None, '') else None
    return out


def load_rows(run_dir: Path) -> tuple[list[dict], list[dict]]:
    """(successful rows sorted by seq, error rows). Later rows win per seq."""
    path = run_dir / 'requests.csv'
    if not path.exists():
        return [], []
    ok: dict[int, dict] = {}
    errs = []
    with path.open(newline='') as f:
        for r in csv.DictReader(f):
            t = _typed(r)
            if r['error']:
                errs.append(t)
            else:
                ok[t['seq']] = t
    errs = [e for e in errs if e['seq'] not in ok]
    return [ok[k] for k in sorted(ok)], errs


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))


def _rate(a: int, b: int) -> float | None:
    return a / b if b else None


def checkpoint_stats(rows: list[dict], model: str, embed_model: str) -> dict:
    n = len(rows)
    hits = [r for r in rows if r['cache'] == 'HIT']
    std = [r for r in hits if r['hit_type'] == 'standard']
    sem = [r for r in hits if r['hit_type'] == 'semantic']
    misses = [r for r in rows if r['cache'] == 'MISS']
    nocache = [r for r in rows if r['cache'] not in ('HIT', 'MISS')]

    def tok(rs, col):
        return sum(r[col] or 0 for r in rs)

    saved_in, saved_out = tok(hits, 'saved_input_tokens'), tok(hits, 'saved_output_tokens')
    billed_in, billed_out = tok(rows, 'billed_input_tokens'), tok(rows, 'billed_output_tokens')
    embed = tok(rows, 'embed_tokens')
    pin, pout = price_for(model)
    pemb, _ = price_for(embed_model)
    # Without a cache every request is a miss. A hit's replayed usage stands
    # in for what that request would have cost.
    base_cost = ((billed_in + saved_in) * pin + (billed_out + saved_out) * pout) / 1e6
    llm_cost = (billed_in * pin + billed_out * pout) / 1e6
    embed_cost = embed * pemb / 1e6

    def per_hit(rs, col):
        return tok(rs, col) / len(rs) if rs else None

    def lat(rs):
        v = [r['latency_ms'] for r in rs if r['latency_ms'] is not None]
        return {'n': len(v), 'p50': pct(v, 50), 'p95': pct(v, 95)}

    return {
        'requests': n,
        'hits': len(hits),
        'standard_hits': len(std),
        'semantic_hits': len(sem),
        'unknown_hits': len(hits) - len(std) - len(sem),
        'misses': len(misses),
        'no_cache_header': len(nocache),
        'hit_rate': _rate(len(hits), n),
        'standard_hit_rate': _rate(len(std), n),
        'semantic_hit_rate': _rate(len(sem), n),
        'tokens': {
            'saved_input_total': saved_in,
            'saved_output_total': saved_out,
            'saved_input_per_hit': per_hit(hits, 'saved_input_tokens'),
            'saved_output_per_hit': per_hit(hits, 'saved_output_tokens'),
            'saved_input_per_standard_hit': per_hit(std, 'saved_input_tokens'),
            'saved_output_per_standard_hit': per_hit(std, 'saved_output_tokens'),
            'saved_input_per_semantic_hit': per_hit(sem, 'saved_input_tokens'),
            'saved_output_per_semantic_hit': per_hit(sem, 'saved_output_tokens'),
            'billed_input_total': billed_in,
            'billed_output_total': billed_out,
            'miss_input_mean': per_hit(misses, 'billed_input_tokens'),
            'miss_output_mean': per_hit(misses, 'billed_output_tokens'),
            'saved_share': _rate(saved_in + saved_out, saved_in + saved_out + billed_in + billed_out),
        },
        'embedding': {
            'tokens_total': embed,
            'tokens_per_request': embed / n if n else None,
            'embedded_requests': sum(1 for r in rows if (r['embed_tokens'] or 0) > 0),
        },
        'latency_ms': {
            'hit': lat(hits),
            'standard_hit': lat(std),
            'semantic_hit': lat(sem),
            'miss': lat(misses),
        },
        'cost_usd': {
            'without_cache': base_cost,
            'llm_with_cache': llm_cost,
            'embedding': embed_cost,
            'with_cache': llm_cost + embed_cost,
            'saved': base_cost - llm_cost - embed_cost,
            'saved_share': _rate(base_cost - llm_cost - embed_cost, base_cost) if base_cost else None,
        },
    }


def checkpoints_for(n: int) -> list[int]:
    cps = [c for c in CHECKPOINTS if c <= n]
    if n and (not cps or cps[-1] != n):
        cps.append(n)
    return cps


def hit_curve(rows: list[dict], bucket: int) -> list[dict]:
    """Hit rate per bucket of requests (not cumulative), for the chart."""
    out = []
    for i in range(0, len(rows), bucket):
        b = rows[i: i + bucket]
        out.append({
            'end': b[-1]['seq'],
            'hit_rate': sum(r['cache'] == 'HIT' for r in b) / len(b),
            'semantic_rate': sum(r['hit_type'] == 'semantic' for r in b) / len(b),
        })
    return out


# --------------------------------------------------------- threshold replay --


def simulate_thresholds(run_dir: Path, rows: list[dict], thresholds: list[float],
                        checkpoints: list[int]) -> dict | None:
    """Replays the cache over the logged embeddings at each threshold.

    Same rules as the gateway: an identical user message is an exact hit;
    otherwise the closest stored entry at or above the threshold is a
    semantic hit; anything else is a miss and is stored. Only misses are
    stored, so a lower threshold also changes what is in the cache later.
    """
    seqs, vecs = VectorLog.read(run_dir, 'embeds')
    if not seqs:
        return None
    by_seq = {}
    for i, s in enumerate(seqs):
        by_seq[s] = i  # last write wins (a retried request logs twice)
    order = [r for r in rows if r['seq'] in by_seq]
    if not order:
        return None
    E = vecs[[by_seq[r['seq']] for r in order]]
    vids = [r['vid'] for r in order]
    n, T = len(order), len(thresholds)
    th = np.asarray(thresholds, dtype=np.float32)[:, None]
    stored = np.zeros((T, n), dtype=bool)
    stored_vids = [set() for _ in range(T)]
    hits = np.zeros((T, n), dtype=bool)
    sem = np.zeros((T, n), dtype=bool)
    block = 256
    for b0 in range(0, n, block):
        b1 = min(n, b0 + block)
        S = E[b0:b1] @ E[:b1].T  # similarities to every earlier request
        for i in range(b0, b1):
            row = S[i - b0, :i]
            for t in range(T):
                if vids[i] in stored_vids[t]:
                    hits[t, i] = True
                    continue
                m = stored[t, :i]
                best = row[m].max() if m.any() else -1.0
                if best >= th[t, 0]:
                    hits[t, i] = sem[t, i] = True
                else:
                    stored[t, i] = True
                    stored_vids[t].add(vids[i])
    out = {'thresholds': thresholds, 'checkpoints': {}}
    seq_arr = np.array([r['seq'] for r in order])
    for cp in checkpoints:
        k = int(np.searchsorted(seq_arr, cp, side='right'))
        out['checkpoints'][str(cp)] = [
            {'threshold': thresholds[t], 'hit_rate': float(hits[t, :k].mean()) if k else None,
             'semantic_rate': float(sem[t, :k].mean()) if k else None}
            for t in range(T)
        ]
    return out


def score_calibration(run_dir: Path, rows: list[dict]) -> dict | None:
    """Gateway match score vs exact cosine to the entry it actually returned.

    The gateway's score is the vector index's row score; this shows whether it
    is the same scale as plain cosine, which the threshold replay assumes.
    """
    seqs, vecs = VectorLog.read(run_dir, 'embeds')
    if not seqs:
        return None
    at = {s: i for i, s in enumerate(seqs)}
    pairs = [
        (r['gateway_score'], float(vecs[at[r['seq']]] @ vecs[at[r['matched_seq']]]))
        for r in rows
        if r['hit_type'] == 'semantic' and r['gateway_score'] is not None
        and r['matched_seq'] in at and r['seq'] in at
    ]
    if len(pairs) < 2:
        return {'pairs': len(pairs)}
    g = np.array([p[0] for p in pairs])
    c = np.array([p[1] for p in pairs])
    corr = float(np.corrcoef(g, c)[0, 1]) if g.std() and c.std() else None
    return {
        'pairs': len(pairs),
        'mean_gateway': float(g.mean()),
        'mean_cosine': float(c.mean()),
        'mean_abs_diff': float(np.abs(g - c).mean()),
        'max_abs_diff': float(np.abs(g - c).max()),
        'correlation': corr,
    }


def simulate_at(run_dir: Path, rows: list[dict], threshold: float) -> float | None:
    """Replay at the threshold the gateway applied, as a check on the replay."""
    sim = simulate_thresholds(run_dir, rows, [threshold], [rows[-1]['seq']] if rows else [])
    if not sim:
        return None
    return next(iter(sim['checkpoints'].values()))[0]['hit_rate']


# ---------------------------------------------------------------- the report --


def load_judgements(run_dir: Path) -> list[dict]:
    path = run_dir / 'judge.jsonl'
    if not path.exists():
        return []
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    review = {}
    rpath = run_dir / 'review.json'
    if rpath.exists():
        review = json.loads(rpath.read_text())
    for j in rows:
        j['human'] = review.get(str(j['seq']))
        j['final'] = j['human'] or j['verdict']
    return rows


def wrong_hit_summary(judged: list[dict]) -> dict:
    n = len(judged)
    wrong = [j for j in judged if j['final'] == 'wrong']
    unsure = [j for j in judged if j['final'] == 'unsure']
    return {
        'sampled': n,
        'wrong': len(wrong),
        'unsure': len(unsure),
        'wrong_rate': _rate(len(wrong), n),
        'judge_wrong': sum(j['verdict'] == 'wrong' for j in judged),
        'human_reviewed': sum(1 for j in judged if j['human']),
        'judge_model': judged[0].get('judge_model') if judged else None,
        'examples': [
            {k: j.get(k) for k in ('seq', 'question', 'matched_question', 'score', 'reason', 'final')}
            for j in wrong[:15]
        ],
    }


def spot_check_summary(run_dir: Path, rows: list[dict]) -> dict | None:
    """The person's spot-check flags (spotcheck.json), kept apart from measure 5.

    Spot-check cards are drawn with filters a person chose, so they are not a
    random sample of semantic hits; they never feed the wrong-hit rate.
    """
    path = run_dir / 'spotcheck.json'
    if not path.exists():
        return None
    try:
        flags = json.loads(path.read_text())
    except ValueError:
        return None
    if not flags:
        return None
    by_seq = {r['seq']: r for r in rows}

    def kind(r):
        if not r:
            return 'unknown'
        return r['hit_type'] or ('miss' if r['cache'] == 'MISS' else 'unknown')

    checked = {int(k): v for k, v in flags.items()}
    wrong = {k: v for k, v in checked.items() if v.get('flag') == 'wrong'}
    by_kind: dict[str, dict] = {}
    for seq, f in checked.items():
        k = kind(by_seq.get(seq))
        d = by_kind.setdefault(k, {'checked': 0, 'wrong': 0})
        d['checked'] += 1
        d['wrong'] += f.get('flag') == 'wrong'
    return {
        'checked': len(checked),
        'wrong': len(wrong),
        'by_kind': by_kind,
        'examples': [
            {'seq': seq, 'kind': kind(by_seq.get(seq)),
             'question': (by_seq.get(seq) or {}).get('question'),
             'score': (by_seq.get(seq) or {}).get('gateway_score'),
             'note': f.get('note', '')}
            for seq, f in sorted(wrong.items())[:20]
        ],
    }


def build(run_id: str, simulate: bool = True) -> dict:
    run_dir = RUNS_DIR / run_id
    meta = json.loads((run_dir / 'meta.json').read_text())
    ensure_response_column(run_dir)  # older runs kept answers only in answers.jsonl
    rows, errs = load_rows(run_dir)
    cps = checkpoints_for(len(rows))
    stats = {}
    for cp in cps:
        upto = [r for r in rows if r['seq'] <= cp]
        stats[str(cp)] = checkpoint_stats(upto, meta['model'], meta['embed_model'])
    # Hit rate within each segment: the slope to extrapolate to larger volumes.
    segments = []
    prev = 0
    for cp in cps:
        seg = [r for r in rows if prev < r['seq'] <= cp]
        if seg:
            segments.append({
                'from': prev + 1, 'to': cp, 'requests': len(seg),
                'hit_rate': sum(r['cache'] == 'HIT' for r in seg) / len(seg),
                'semantic_hit_rate': sum(r['hit_type'] == 'semantic' for r in seg) / len(seg),
            })
        prev = cp

    sim = None
    replay_check = None
    applied = _float(meta.get('applied_threshold')) or _float(meta.get('requested_threshold'))
    if simulate and rows:
        ths = sorted(set(THRESHOLDS + ([round(applied, 4)] if applied else [])))
        # The replay is the slow part; reuse it when only the review changed.
        prev = _previous(run_dir)
        if prev.get('requests_ok') == len(rows) and prev.get('thresholds', {}) and \
                prev['thresholds'].get('thresholds') == ths:
            sim = prev['thresholds']
        else:
            sim = simulate_thresholds(run_dir, rows, ths, cps)
        if sim and applied:
            final = sim['checkpoints'][str(cps[-1])]
            at = next(x for x in final if abs(x['threshold'] - round(applied, 4)) < 1e-9)
            replay_check = {
                'threshold': applied,
                'replayed_hit_rate': at['hit_rate'],
                'actual_hit_rate': stats[str(cps[-1])]['hit_rate'],
            }

    # Agreement between the gateway and the shadow index on each request.
    shadow_agree = None
    if applied:
        nonstd = [r for r in rows if r['hit_type'] != 'standard' and r['shadow_score'] is not None]
        tp = sum(1 for r in nonstd if r['cache'] == 'HIT' and r['shadow_score'] >= applied)
        fn = sum(1 for r in nonstd if r['cache'] == 'HIT' and r['shadow_score'] < applied)
        fp = sum(1 for r in nonstd if r['cache'] != 'HIT' and r['shadow_score'] >= applied)
        tn = len(nonstd) - tp - fn - fp
        shadow_agree = {'both_hit': tp, 'gateway_only': fn, 'shadow_only': fp, 'both_miss': tn}

    calibration = score_calibration(run_dir, rows) if rows else None
    sem_scores = [r['gateway_score'] for r in rows
                  if r['hit_type'] == 'semantic' and r['gateway_score'] is not None]
    judged = load_judgements(run_dir)
    progress = {}
    if (run_dir / 'progress.json').exists():
        progress = json.loads((run_dir / 'progress.json').read_text())
    rep = {
        'run_id': run_id,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'meta': {k: v for k, v in meta.items() if k not in ('first_response_headers',)},
        'progress': progress,
        'requests_ok': len(rows),
        'requests_failed': len(errs),
        'retried_requests': sum(1 for r in rows if (r['attempts'] or 1) > 1),
        'checkpoints': stats,
        'segments': segments,
        'curve': hit_curve(rows, max(50, len(rows) // 80)) if rows else [],
        'thresholds': sim,
        'replay_check': replay_check,
        'shadow_agreement': shadow_agree,
        'score_calibration': calibration,
        'semantic_score': {
            'min': min(sem_scores) if sem_scores else None,
            'p50': pct(sem_scores, 50),
            'max': max(sem_scores) if sem_scores else None,
        },
        'wrong_hits': wrong_hit_summary(judged) if judged else None,
        'spot_check': spot_check_summary(run_dir, rows),
    }
    (run_dir / 'report.json').write_text(json.dumps(rep, indent=2))
    (run_dir / 'report.md').write_text(render_md([rep]))
    (run_dir / 'report.html').write_text(render_html([rep], subtitle=f'Run {run_id}'))
    return rep


def _previous(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / 'report.json').read_text())
    except (OSError, ValueError):
        return {}


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def build_summary(run_ids: list[str], out: Path | None = None) -> Path:
    reps = []
    for rid in run_ids:
        p = RUNS_DIR / rid / 'report.json'
        reps.append(json.loads(p.read_text()) if p.exists() else build(rid))
    out = out or RUNS_DIR / 'summary'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(reps, indent=2))
    (out / 'summary.md').write_text(render_md(reps))
    (out / 'summary.html').write_text(render_html(
        reps, subtitle=f'Summary of {len(reps)} run' + ('s' if len(reps) != 1 else '')))
    return out


# ----------------------------------------------------------------- rendering --

WORKLOAD_NAMES = {'chatbot': 'A. Chatbot / FAQ assistant', 'rag': 'B. Agentic / RAG assistant'}


def _n(v, d=0) -> str:
    if v is None:
        return '—'
    if isinstance(v, float) and math.isnan(v):
        return '—'
    return f'{v:,.{d}f}'


def _p(v, d=1) -> str:
    return '—' if v is None else f'{v * 100:.{d}f}%'


def _usd(v) -> str:
    return '—' if v is None else f'${v:,.2f}'


def run_title(rep: dict) -> str:
    m = rep['meta']
    return f'{WORKLOAD_NAMES.get(m["workload"], m["workload"])} — {m["model"]}'


def measure_rows(rep: dict) -> list[tuple[str, list[str]]]:
    """(label, value per checkpoint) for the main table, measures 1-4."""
    cps = list(rep['checkpoints'].values())

    def row(label, f):
        return (label, [f(c) for c in cps])

    return [
        row('Requests', lambda c: _n(c['requests'])),
        row('Hits (all)', lambda c: _n(c['hits'])),
        row('— standard (exact) hits', lambda c: _n(c['standard_hits'])),
        row('— semantic hits', lambda c: _n(c['semantic_hits'])),
        row('Hit rate', lambda c: _p(c['hit_rate'])),
        row('— standard hit rate', lambda c: _p(c['standard_hit_rate'])),
        row('— semantic hit rate', lambda c: _p(c['semantic_hit_rate'])),
        row('Input tokens saved per hit', lambda c: _n(c['tokens']['saved_input_per_hit'], 1)),
        row('Output tokens saved per hit', lambda c: _n(c['tokens']['saved_output_per_hit'], 1)),
        row('Input tokens saved, total', lambda c: _n(c['tokens']['saved_input_total'])),
        row('Output tokens saved, total', lambda c: _n(c['tokens']['saved_output_total'])),
        row('Input / output tokens per miss', lambda c: (
            f'{_n(c["tokens"]["miss_input_mean"])} / {_n(c["tokens"]["miss_output_mean"])}')),
        row('Embedding tokens (semantic cache), total', lambda c: _n(c['embedding']['tokens_total'])),
        row('Embedding tokens per request', lambda c: _n(c['embedding']['tokens_per_request'], 1)),
        row('Latency p50 / p95, hits (ms)', lambda c: (
            f'{_n(c["latency_ms"]["hit"]["p50"])} / {_n(c["latency_ms"]["hit"]["p95"])}')),
        row('— semantic hits (ms)', lambda c: (
            f'{_n(c["latency_ms"]["semantic_hit"]["p50"])} / '
            f'{_n(c["latency_ms"]["semantic_hit"]["p95"])}')),
        row('— standard hits (ms)', lambda c: (
            f'{_n(c["latency_ms"]["standard_hit"]["p50"])} / '
            f'{_n(c["latency_ms"]["standard_hit"]["p95"])}')),
        row('Latency p50 / p95, misses (ms)', lambda c: (
            f'{_n(c["latency_ms"]["miss"]["p50"])} / {_n(c["latency_ms"]["miss"]["p95"])}')),
        row('Cost without cache (list price)', lambda c: _usd(c['cost_usd']['without_cache'])),
        row('Cost with cache (LLM + embeddings)', lambda c: _usd(c['cost_usd']['with_cache'])),
        row('Saved', lambda c: (
            f'{_usd(c["cost_usd"]["saved"])} ({_p(c["cost_usd"]["saved_share"])})')),
    ]


def settings_rows(rep: dict) -> list[tuple[str, str]]:
    m = rep['meta']
    ds = m.get('dataset', {})
    st = ds.get('stats', {})
    return [
        ('Model', m['model']),
        ('Embedding model (cache)', m['embed_model']),
        ('Gateway', m['gateway_url']),
        ('Cache type', m.get('cache_type', 'semantic')),
        ('Similarity threshold applied', str(m.get('applied_threshold') or '— (no debug header)')),
        ('Expiry applied (gateway)', str(m.get('applied_expiry') or '—')),
        ('Expiry requested', f'{m["requested_expiry_s"]} s'),
        ('Concurrency', str(m.get('concurrency'))),
        ('Started / finished', f'{m.get("started_at", "—")} / {m.get("finished_at", "—")}'),
        ('Dataset', ds.get('source', '—')),
        ('Popularity model', '; '.join(f'{k}: {v}' for k, v in ds.get('popularity', {}).items())),
        ('Distinct messages / groups in stream', f'{_n(st.get("distinct_messages"))} / '
                                                 f'{_n(st.get("distinct_groups"))}'),
        ('Exact repeats / rewordings in stream', f'{_p(st.get("exact_repeat_share"))} / '
                                                 f'{_p(st.get("reworded_repeat_share"))}'),
        ('Requests OK / failed / retried', f'{_n(rep["requests_ok"])} / {_n(rep["requests_failed"])}'
                                            f' / {_n(rep["retried_requests"])}'),
    ]


def render_md(reps: list[dict]) -> str:
    out = [f'# {TITLE}', '']
    for rep in reps:
        cps = list(rep['checkpoints'])
        out += [f'## {run_title(rep)}', '', f'Run `{rep["run_id"]}`', '']
        out += ['| Setting | Value |', '|---|---|']
        out += [f'| {k} | {v} |' for k, v in settings_rows(rep)]
        out += ['', '| Measure | ' + ' | '.join(f'after {int(c):,}' for c in cps) + ' |',
                '|---|' + '---:|' * len(cps)]
        out += [f'| {label} | ' + ' | '.join(vals) + ' |' for label, vals in measure_rows(rep)]
        if rep['segments']:
            out += ['', 'Hit rate within each segment:', '']
            out += [f'- requests {s["from"]:,}–{s["to"]:,}: {_p(s["hit_rate"])} '
                    f'(semantic {_p(s["semantic_hit_rate"])})' for s in rep['segments']]
        w = rep.get('wrong_hits')
        out += ['', '### Wrong hits', '']
        if w:
            out += [f'{w["wrong"]} of {w["sampled"]} sampled semantic hits returned an answer that '
                    f'did not fit the question ({_p(w["wrong_rate"])}); {w["unsure"]} unsure. '
                    f'Judge: {w["judge_model"]}; human-reviewed: {w["human_reviewed"]}.', '']
            for e in w['examples']:
                out.append(f'- #{e["seq"]} "{e["question"]}" got the answer to '
                           f'"{e["matched_question"]}" (score {e["score"]}): {e["reason"]}')
        else:
            out.append('Not judged yet (`python -m savings judge`).')
        sc = rep.get('spot_check')
        if sc:
            kinds = ', '.join(f'{k}: {v["wrong"]}/{v["checked"]}' for k, v in sc['by_kind'].items())
            out += ['', '### Spot check (manual, not part of the wrong-hit rate)', '',
                    f'{sc["wrong"]} of {sc["checked"]} checked requests marked wrong ({kinds}).']
            for e in sc['examples']:
                out.append(f'- #{e["seq"]} ({e["kind"]}) "{e["question"]}"'
                           + (f': {e["note"]}' if e['note'] else ''))
        sim = rep.get('thresholds')
        if sim:
            last = list(sim['checkpoints'])[-1]
            out += ['', f'### Hit rate at other thresholds (replay, after {int(last):,})', '',
                    '| Threshold | ' + ' | '.join(f'after {int(c):,}' for c in sim['checkpoints'])
                    + ' |', '|---|' + '---:|' * len(sim['checkpoints'])]
            for i, t in enumerate(sim['thresholds']):
                out.append(f'| {t:.2f} | ' + ' | '.join(
                    _p(v[i]['hit_rate']) for v in sim['checkpoints'].values()) + ' |')
            rc = rep.get('replay_check')
            if rc:
                out += ['', f'Replay check at the applied threshold {rc["threshold"]}: replay '
                        f'{_p(rc["replayed_hit_rate"])} vs measured {_p(rc["actual_hit_rate"])}.']
        out.append('')
    out += ['Raw per-request data: `requests.csv` in each run directory.', '']
    return '\n'.join(out)


def _svg_lines(series: list[tuple[str, str, list[tuple[float, float]]]], xlabel: str,
               ylabel: str, xmax: float, xmin: float = 0.0, w: int = 640, h: int = 240) -> str:
    """Small inline line chart. series: (name, css color var, [(x, y in 0..1)])."""
    pl, pr, pt, pb = 48, 12, 12, 34
    iw, ih = w - pl - pr, h - pt - pb
    ymax = max([y for _, _, pts in series for _, y in pts] + [0.05])
    ymax = min(1.0, math.ceil(ymax * 10) / 10)

    def X(x):
        return pl + iw * (x - xmin) / (xmax - xmin) if xmax > xmin else pl

    def Y(y):
        return pt + ih * (1 - y / ymax)

    g = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{html.escape(ylabel)}">']
    for k in range(6):
        y = ymax * k / 5
        g.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(y):.1f}" y2="{Y(y):.1f}" class="grid"/>')
        g.append(f'<text x="{pl - 6}" y="{Y(y) + 4:.1f}" class="tick" text-anchor="end">'
                 f'{y * 100:.0f}%</text>')
    for k in range(5):
        x = xmin + (xmax - xmin) * k / 4
        g.append(f'<text x="{X(x):.1f}" y="{h - 14}" class="tick" text-anchor="middle">'
                 f'{x:,.0f}</text>' if xmax > 2 else
                 f'<text x="{X(x):.1f}" y="{h - 14}" class="tick" text-anchor="middle">'
                 f'{x:.2f}</text>')
    g.append(f'<text x="{pl + iw / 2}" y="{h - 1}" class="axis" text-anchor="middle">'
             f'{html.escape(xlabel)}</text>')
    for name, color, pts in series:
        if not pts:
            continue
        d = ' '.join(f'{"M" if i == 0 else "L"}{X(x):.1f},{Y(y):.1f}' for i, (x, y) in enumerate(pts))
        g.append(f'<path d="{d}" fill="none" stroke="var({color})" stroke-width="2">'
                 f'<title>{html.escape(name)}</title></path>')
    g.append('</svg>')
    legend = ''.join(f'<span class="key"><i style="background:var({c})"></i>{html.escape(n)}</span>'
                     for n, c, _ in series)
    return f'<figure>{"".join(g)}<figcaption>{legend}</figcaption></figure>'


CSS = """
:root{--bg:#fff;--fg:#1b1f24;--muted:#5d6670;--line:#e3e6ea;--card:#f7f8fa;--accent:#c4262e;
--s1:#c4262e;--s2:#1f6feb;--s3:#8250df;--s4:#1a7f37;--good:#1a7f37;--bad:#c4262e}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#111418;--fg:#e6e8eb;
--muted:#9aa3ad;--line:#2a3038;--card:#181c21;--s1:#ff6b6b;--s2:#58a6ff;--s3:#bc8cff;--s4:#3fb950;
--good:#3fb950;--bad:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 8px;padding-top:12px;
border-top:2px solid var(--accent)}h3{font-size:15px;margin:22px 0 6px}
.sub{color:var(--muted);margin:0 0 16px}.kicker{font-size:16px;font-weight:600;margin:0 0 4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:8px 0;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}thead th{color:var(--muted);font-weight:600}
td.sub-row{padding-left:20px;color:var(--muted)}
.settings td{text-align:left;white-space:normal}.settings td:first-child{color:var(--muted);width:34%}
figure{margin:8px 0 16px}.chart{width:100%;max-width:680px;height:auto}
.grid{stroke:var(--line)}.tick,.axis{fill:var(--muted);font-size:11px}
figcaption{color:var(--muted);font-size:12px}.key{margin-right:14px}
.key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.note{color:var(--muted);font-size:13px}ul.ex{padding-left:18px}ul.ex li{margin:4px 0}
code{font-size:12px}
"""


def render_html(reps: list[dict], subtitle: str) -> str:
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
             f'<meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>{html.escape(TITLE)} · {html.escape(subtitle)}</title><style>{CSS}</style></head>'
             '<body><main>',
             f'<h1>{html.escape(TITLE)}</h1>',
             f'<p class="kicker">{html.escape(subtitle)}</p>',
             f'<p class="sub">Semantic cache in the Serverless Model Service gateway. Checkpoints count requests '
             f'from the start of one stream inside one cache expiry window. Generated '
             f'{html.escape(reps[0]["generated_at"]) if reps else ""}.</p>']
    if len(reps) > 1:
        parts.append(_overview(reps))
    for rep in reps:
        parts.append(_run_html(rep))
    parts.append(_method_html())
    parts.append('</main></body></html>')
    return ''.join(parts)


def _overview(reps: list[dict]) -> str:
    rows = []
    for rep in reps:
        cps = rep['checkpoints']
        last = cps[list(cps)[-1]]
        cells = ''.join(f'<td>{_p(c["hit_rate"])}</td>' for c in cps.values())
        rows.append(
            f'<tr><td>{html.escape(run_title(rep))}</td>{cells}'
            f'<td>{_n(last["tokens"]["saved_input_per_hit"])} / '
            f'{_n(last["tokens"]["saved_output_per_hit"])}</td>'
            f'<td>{_p(last["cost_usd"]["saved_share"])}</td>'
            f'<td>{_p(rep["wrong_hits"]["wrong_rate"]) if rep.get("wrong_hits") else "—"}</td></tr>')
    heads = ''.join(f'<th>hit rate @{int(c):,}</th>' for c in reps[0]['checkpoints'])
    return ('<h2>Overview</h2><div class="scroll"><table><thead><tr><th>Run</th>' + heads +
            '<th>tokens saved / hit (in / out)</th><th>cost saved</th><th>wrong hits</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def _run_html(rep: dict) -> str:
    cps = list(rep['checkpoints'])
    last = rep['checkpoints'][cps[-1]] if cps else None
    h = [f'<h2>{html.escape(run_title(rep))}</h2>',
         f'<p class="sub">Run <code>{html.escape(rep["run_id"])}</code> · '
         f'state {html.escape(rep.get("progress", {}).get("state", rep["meta"].get("status", "")))}</p>']
    if not last:
        return ''.join(h) + '<p>No requests yet.</p>'
    w = rep.get('wrong_hits')
    tiles = [
        (_p(last['hit_rate']), f'hit rate after {int(cps[-1]):,}'),
        (f'{_p(last["standard_hit_rate"])} / {_p(last["semantic_hit_rate"])}', 'standard / semantic'),
        (f'{_n(last["tokens"]["saved_input_per_hit"])} / {_n(last["tokens"]["saved_output_per_hit"])}',
         'input / output tokens saved per hit'),
        (_p(last['cost_usd']['saved_share']), f'cost saved ({_usd(last["cost_usd"]["saved"])})'),
        (f'{_n(last["latency_ms"]["hit"]["p50"])} vs {_n(last["latency_ms"]["miss"]["p50"])} ms',
         'p50 latency, hit vs miss'),
        (_p(w['wrong_rate']) if w else '—',
         f'wrong hits ({w["wrong"]}/{w["sampled"]}, {w["unsure"]} unsure)' if w
         else 'wrong hits (not judged)'),
    ]
    h.append('<div class="tiles">' + ''.join(
        f'<div class="tile"><b>{html.escape(v)}</b><span>{html.escape(k)}</span></div>'
        for v, k in tiles) + '</div>')
    head = ''.join(f'<th>after {int(c):,}</th>' for c in cps)
    body = []
    for label, vals in measure_rows(rep):
        cls = ' class="sub-row"' if label.startswith('—') else ''
        body.append(f'<tr><td{cls}>{html.escape(label.lstrip("— "))}</td>' +
                    ''.join(f'<td>{html.escape(v)}</td>' for v in vals) + '</tr>')
    h.append('<h3>Measures 1–4 at each checkpoint</h3><div class="scroll"><table><thead><tr>'
             '<th>Measure</th>' + head + '</tr></thead><tbody>' + ''.join(body) +
             '</tbody></table></div>')
    if rep['segments']:
        h.append('<p class="note">Hit rate within each segment: ' + '; '.join(
            f'{s["from"]:,}–{s["to"]:,}: {_p(s["hit_rate"])}' for s in rep['segments']) + '.</p>')
    if rep['curve']:
        pts_all = [(c['end'], c['hit_rate']) for c in rep['curve']]
        pts_sem = [(c['end'], c['semantic_rate']) for c in rep['curve']]
        h.append(_svg_lines([('All hits', '--s1', pts_all), ('Semantic hits', '--s2', pts_sem)],
                            'requests into the stream', 'hit rate per segment',
                            float(rep['curve'][-1]['end'])))

    h.append('<h3>Measure 5: wrong hits</h3>')
    if w:
        h.append(f'<p>{w["wrong"]} of {w["sampled"]} sampled semantic hits returned an answer that '
                 f'did not fit the question ({_p(w["wrong_rate"])}); {w["unsure"]} unsure. Judge model '
                 f'{html.escape(str(w["judge_model"]))}; {w["human_reviewed"]} verdicts set by a human.</p>')
        if w['examples']:
            h.append('<ul class="ex">' + ''.join(
                f'<li><b>#{e["seq"]}</b> “{html.escape(str(e["question"]))}” got the cached answer to '
                f'“{html.escape(str(e["matched_question"]))}” (score {e["score"]}). '
                f'{html.escape(str(e["reason"]))}</li>' for e in w['examples']) + '</ul>')
    else:
        h.append('<p class="note">Not judged yet.</p>')

    sc = rep.get('spot_check')
    if sc:
        kinds = '; '.join(f'{html.escape(k)} {v["wrong"]}/{v["checked"]}' for k, v in sc['by_kind'].items())
        h.append('<h3>Spot check</h3><p class="note">Requests a person looked at in the Spot check tab, '
                 'drawn with filters they chose. Not a random sample, so not part of the wrong-hit rate '
                 'above.</p>')
        h.append(f'<p>{sc["wrong"]} of {sc["checked"]} checked requests marked wrong ({kinds}).</p>')
        if sc['examples']:
            h.append('<ul class="ex">' + ''.join(
                f'<li><b>#{e["seq"]}</b> ({html.escape(e["kind"])}) “{html.escape(str(e["question"]))}”'
                + (f'. {html.escape(e["note"])}' if e['note'] else '') + '</li>'
                for e in sc['examples']) + '</ul>')
    sim = rep.get('thresholds')
    if sim:
        cpk = list(sim['checkpoints'])
        h.append('<h3>Hit rate at other similarity thresholds</h3>'
                 '<p class="note">Replayed from the logged embeddings of every request (see Method). '
                 'Lower thresholds hit more often, and the wrong-hit rate rises with them.</p>')
        head = ''.join(f'<th>after {int(c):,}</th>' for c in cpk)
        rows = []
        applied = rep.get('replay_check', {}) or {}
        for i, t in enumerate(sim['thresholds']):
            mark = ' (applied)' if applied and abs(t - round(applied.get('threshold', -1), 4)) < 1e-9 else ''
            rows.append(f'<tr><td>{t:.2f}{mark}</td>' + ''.join(
                f'<td>{_p(sim["checkpoints"][c][i]["hit_rate"])}</td>' for c in cpk) + '</tr>')
        h.append('<div class="scroll"><table><thead><tr><th>Threshold</th>' + head +
                 '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
        final = sim['checkpoints'][cpk[-1]]
        h.append(_svg_lines(
            [('Hit rate', '--s1', [(x['threshold'], x['hit_rate']) for x in final]),
             ('Semantic hit rate', '--s2', [(x['threshold'], x['semantic_rate']) for x in final])],
            'similarity threshold', 'hit rate by threshold',
            xmax=max(x['threshold'] for x in final), xmin=min(x['threshold'] for x in final)))
        if applied:
            h.append(f'<p class="note">Check: replay at the applied threshold {applied["threshold"]} '
                     f'gives {_p(applied["replayed_hit_rate"])}; the gateway measured '
                     f'{_p(applied["actual_hit_rate"])}.</p>')
    sa = rep.get('shadow_agreement')
    if sa:
        h.append(f'<p class="note">Closest-match scores vs the gateway on non-exact requests: both hit '
                 f'{sa["both_hit"]:,}, gateway only {sa["gateway_only"]:,}, shadow only '
                 f'{sa["shadow_only"]:,}, both miss {sa["both_miss"]:,}.</p>')
    cal = rep.get('score_calibration')
    if cal and cal.get('mean_abs_diff') is not None:
        h.append(f'<p class="note">Score scale check on {cal["pairs"]:,} semantic hits: gateway score '
                 f'mean {cal["mean_gateway"]:.4f} vs cosine to the returned entry {cal["mean_cosine"]:.4f} '
                 f'(mean |diff| {cal["mean_abs_diff"]:.4f}, max {cal["max_abs_diff"]:.4f}, '
                 f'r={_n(cal["correlation"], 3)}). A small difference means the threshold replay is on the '
                 f'gateway\'s own scale.</p>')
    h.append('<h3>Settings</h3><div class="scroll"><table class="settings"><tbody>' + ''.join(
        f'<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>'
        for k, v in settings_rows(rep)) + '</tbody></table></div>')
    h.append('<p class="note">Measure 6, the raw per-request data, is <code>requests.csv</code> in '
             'the run directory.</p>')
    return ''.join(h)


def _method_html() -> str:
    return """<h2>Method</h2>
<ul>
<li><b>Stream.</b> Each run sends one fixed, seeded stream of requests in order (a small worker pool
keeps a handful in flight). Every request carries <code>X-cb-cache: semantic</code>,
<code>X-cb-debug: true</code> and an expiry long enough for the whole run. A per-run tag in the system
prompt keeps runs from hitting each other's entries (the system prompt is part of the cache key).</li>
<li><b>Standard vs semantic.</b> The gateway tries an exact lookup before the vector search. A HIT
replays the stored response with its original <code>id</code>, which names the request that stored
it: the same user message means a standard (exact) hit, a different one a semantic hit.</li>
<li><b>Tokens saved.</b> A HIT replays the stored <code>usage</code>, which is what Bedrock reported
when the answer was first generated and cached. That is counted as saved input and output.</li>
<li><b>Embedding tokens.</b> In semantic mode the gateway embeds the last user message on every
request that is not an exact hit, and reuses that vector when it stores a miss. The harness embeds
the same text with the same model through the gateway's <code>/v1/embeddings</code> and records the
token count Bedrock returns.</li>
<li><b>Closest-match score.</b> The gateway reports a score only on a hit, so the harness keeps its
own copy of the stored vectors and logs the closest match for every request
(<code>shadow_score</code>). Its search is exact where the gateway's index is approximate.</li>
<li><b>Other thresholds.</b> The report replays the cache over all logged embeddings at each
threshold, with the gateway's rules (exact first, then the closest stored entry, misses are stored).
The replay at the applied threshold is shown next to the measured rate as a check.</li>
<li><b>Latency.</b> Client-measured time for the chat request, from the harness host.</li>
<li><b>Wrong hits.</b> A seeded sample of 100 semantic hits is judged by an LLM with caching off:
does the returned answer fit the question asked? A person can override each verdict in the UI.</li>
<li><b>Cost.</b> List prices per million tokens: Sonnet 4.6 $3 / $15, Haiku 4.5 $1 / $5, Titan
embeddings v2 $0.02. Cost without cache counts a hit's replayed usage as what it would have cost.</li>
</ul>"""
