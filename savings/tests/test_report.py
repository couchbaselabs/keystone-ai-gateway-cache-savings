import csv
import json

import numpy as np
import pytest

from savings import report
from savings.config import public_gateway_url
from savings.csvlog import COLUMNS, ensure_response_column
from savings.runner import classify
from savings.shadow import ShadowIndex, VectorLog


def row(seq, cache, hit_type='', vid=None, pin=400, pout=200, lat=1000.0, emb=10):
    hit = cache == 'HIT'
    return {
        'seq': seq, 'vid': vid or f'v{seq}', 'cache': cache, 'hit_type': hit_type,
        'prompt_tokens': pin, 'completion_tokens': pout,
        'saved_input_tokens': pin if hit else 0, 'saved_output_tokens': pout if hit else 0,
        'billed_input_tokens': 0 if hit else pin, 'billed_output_tokens': 0 if hit else pout,
        'embed_tokens': 0 if hit_type == 'standard' else emb,
        'latency_ms': lat, 'gateway_score': None, 'shadow_score': None, 'attempts': 1,
    }


def test_classify():
    assert classify('MISS', None, 'a', None) == ''
    assert classify('HIT', 0.93, 'a', 'b') == 'semantic'
    assert classify('HIT', 1.0, 'a', 'a') == 'standard'
    # The replayed id wins over the score.
    assert classify('HIT', 1.0, 'a', 'b') == 'semantic'
    assert classify('HIT', 1.0, 'a', None) == 'standard'
    assert classify('HIT', 0.95, 'a', None) == 'semantic'
    assert classify('HIT', None, 'a', None) == 'unknown'


def test_checkpoint_stats_counts_tokens_latency_and_cost():
    rows = [
        row(1, 'MISS', lat=2000), row(2, 'MISS', lat=4000),
        row(3, 'HIT', 'standard', lat=50), row(4, 'HIT', 'semantic', pin=300, pout=100, lat=150),
    ]
    s = report.checkpoint_stats(rows, 'x-sonnet-4-6', 'titan-embed')
    assert (s['requests'], s['hits'], s['standard_hits'], s['semantic_hits']) == (4, 2, 1, 1)
    assert s['hit_rate'] == 0.5 and s['semantic_hit_rate'] == 0.25
    t = s['tokens']
    assert t['saved_input_total'] == 700 and t['saved_output_total'] == 300
    assert t['saved_input_per_hit'] == 350 and t['saved_output_per_semantic_hit'] == 100
    assert t['billed_input_total'] == 800 and t['miss_output_mean'] == 200
    # Exact hits never embed: 3 requests x 10 tokens.
    assert s['embedding']['tokens_total'] == 30
    assert s['latency_ms']['miss']['p50'] == 3000
    assert s['latency_ms']['hit']['p50'] == 100
    c = s['cost_usd']
    assert c['without_cache'] == pytest.approx((1500 * 3 + 700 * 15) / 1e6)
    assert c['llm_with_cache'] == pytest.approx((800 * 3 + 400 * 15) / 1e6)
    assert c['embedding'] == pytest.approx(30 * 0.02 / 1e6)


def test_checkpoints_for():
    assert report.checkpoints_for(50) == [50]
    assert report.checkpoints_for(1000) == [1000]
    assert report.checkpoints_for(12000) == [1000, 10000, 12000]
    assert report.checkpoints_for(20000) == [1000, 10000, 20000]


def test_shadow_index_closest():
    idx = ShadowIndex()
    assert idx.closest([1, 0]) == (None, None)
    idx.add(1, [1, 0])
    idx.add(2, [0, 3])
    score, seq = idx.closest([0.1, 1])
    assert seq == 2 and score == pytest.approx(1 / np.sqrt(1.01), rel=1e-5)
    for i in range(3, 3000):  # grows past the initial capacity
        idx.add(i, [1, 1])
    assert idx.n == 2999


def _write_run(tmp_path, rows, vecs):
    run_dir = tmp_path / 'r'
    run_dir.mkdir()
    log = VectorLog(run_dir, 'embeds')
    for r, v in zip(rows, vecs):
        v = np.asarray(v, dtype=np.float32)
        log.append(r['seq'], v / np.linalg.norm(v))
    log.close()
    return run_dir


def test_threshold_replay_follows_gateway_rules(tmp_path):
    # a, a (exact), b close to a (0.95), c far, c' close to c (0.85)
    a = [1, 0, 0]
    b = [0.95, np.sqrt(1 - 0.95 ** 2), 0]
    c = [0, 0, 1]
    c2 = [0, np.sqrt(1 - 0.85 ** 2), 0.85]
    rows = [{'seq': i + 1, 'vid': vid} for i, vid in enumerate(['a', 'a', 'b', 'c', 'c2'])]
    run_dir = _write_run(tmp_path, rows, [a, a, b, c, c2])
    sim = report.simulate_thresholds(run_dir, rows, [0.8, 0.9, 0.99], [5])
    by_t = {x['threshold']: x for x in sim['checkpoints']['5']}
    assert by_t[0.8]['hit_rate'] == pytest.approx(3 / 5)  # exact + b + c2
    assert by_t[0.9]['hit_rate'] == pytest.approx(2 / 5)  # exact + b
    assert by_t[0.99]['hit_rate'] == pytest.approx(1 / 5)  # exact only
    assert by_t[0.9]['semantic_rate'] == pytest.approx(1 / 5)


def test_build_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'RUNS_DIR', tmp_path)
    run_dir = tmp_path / 'run1'
    run_dir.mkdir()
    rows = []
    for seq in range(1, 1201):
        if seq % 3 == 0:
            r = row(seq, 'HIT', 'semantic')
        elif seq % 3 == 1:
            r = row(seq, 'MISS')
        else:
            r = row(seq, 'HIT', 'standard')
        rows.append({c: r.get(c, '') for c in COLUMNS})
    rows.append({**{c: '' for c in COLUMNS}, 'seq': 1201, 'error': 'boom'})
    with (run_dir / 'requests.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    meta = {'run_id': 'run1', 'workload': 'chatbot', 'model': 'sonnet', 'embed_model': 'titan-embed',
            'gateway_url': 'http://x', 'requested_expiry_s': 100, 'applied_threshold': None,
            'requested_threshold': None, 'dataset': {}}
    (run_dir / 'meta.json').write_text(json.dumps(meta))
    rep = report.build('run1', simulate=False)
    assert list(rep['checkpoints']) == ['1000', '1200']
    assert rep['requests_failed'] == 1
    assert rep['checkpoints']['1200']['hit_rate'] == pytest.approx(800 / 1200)
    assert (run_dir / 'report.html').read_text().startswith('<!doctype html>')
    assert 'after 1,000' in (run_dir / 'report.md').read_text()


def test_score_calibration(tmp_path):
    rows = [{'seq': 1, 'vid': 'a'}, {'seq': 2, 'vid': 'b'}, {'seq': 3, 'vid': 'c'}]
    run_dir = _write_run(tmp_path, rows, [[1, 0], [0.9, np.sqrt(1 - 0.81)], [0.8, 0.6]])
    rows[1].update(hit_type='semantic', gateway_score=0.9, matched_seq=1)
    rows[2].update(hit_type='semantic', gateway_score=0.85, matched_seq=1)
    rows[0].update(hit_type='', gateway_score=None, matched_seq=None)
    cal = report.score_calibration(run_dir, rows)
    assert cal['pairs'] == 2
    assert cal['mean_cosine'] == pytest.approx(0.85, abs=1e-6)
    assert cal['max_abs_diff'] == pytest.approx(0.05, abs=1e-6)


def test_ensure_response_column_upgrades_old_csv(tmp_path):
    run_dir = tmp_path / 'old'
    run_dir.mkdir()
    old_cols = [c for c in COLUMNS if c != 'response']
    with (run_dir / 'requests.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=old_cols)
        w.writeheader()
        w.writerow({**{c: '' for c in old_cols}, 'seq': 2, 'cache': 'HIT'})
        w.writerow({**{c: '' for c in old_cols}, 'seq': 1, 'cache': 'MISS'})
        w.writerow({**{c: '' for c in old_cols}, 'seq': 3, 'error': 'boom'})
    answers = [{'seq': 1, 'answer': 'Line one,\n"quoted" line two'}, {'seq': 2, 'answer': 'Cached'}]
    (run_dir / 'answers.jsonl').write_text(''.join(json.dumps(a) + '\n' for a in answers))

    assert ensure_response_column(run_dir) is True
    with (run_dir / 'requests.csv').open(newline='') as f:
        rows = list(csv.DictReader(f))
    assert [r['seq'] for r in rows] == ['1', '2', '3']
    assert rows[0]['response'] == 'Line one,\n"quoted" line two'  # multi-line text survives
    assert rows[1]['response'] == 'Cached' and rows[2]['response'] == ''
    assert ensure_response_column(run_dir) is False  # already upgraded


def test_public_gateway_url_hides_deployment_hosts():
    assert public_gateway_url('https://localhost:8081') == 'https://localhost:8081'
    assert public_gateway_url('http://127.0.0.1:18082') == 'http://127.0.0.1:18082'
    hidden = public_gateway_url('https://abc123.ai.sandbox.example.com')
    assert 'example' not in hidden and hidden.startswith('https://')
