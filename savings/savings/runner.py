"""Sends one workload's request stream through the gateway and logs every request.

Run directory (runs/<run-id>/):
  meta.json      settings, dataset description, settings the gateway applied
  requests.csv   one row per request (the raw data for the pricing model)
  answers.jsonl  answer text per request, for the wrong-hit review
  progress.json  live counters for the UI
  shadow.*       client-side copy of the cache's vectors (see shadow.py)

Requests go out in stream order from a small worker pool; with N workers at
most N consecutive requests are in flight, so the stream stays ordered to
within N requests.
"""

from __future__ import annotations

import csv
import json
import secrets
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import CACHE_HEADERS, RUNS_DIR, Settings, public_gateway_url
from .csvlog import COLUMNS, ensure_response_column, write_rows
from .datasets import CHATBOT_SYSTEM, RAG_SYSTEM, load_workload
from .gateway import Gateway, GatewayError
from .shadow import PersistentShadowIndex


SYSTEMS = {'chatbot': CHATBOT_SYSTEM, 'rag': RAG_SYSTEM}

# 48 hours: comfortably longer than one 20,000-request run. Note that the
# gateway's 7-day cap does not apply (ai-gateway metadata.go GetCacheExpiry
# compares seconds with nanoseconds), so whatever is sent here is used.
DEFAULT_EXPIRY_S = 172800


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def classify(cache: str, score: float | None, vid: str, matched_vid: str | None) -> str:
    """standard (exact) or semantic for a HIT, '' otherwise.

    The replayed response id names the request that stored the entry; the
    same user message means the exact lookup matched. Without that, the
    gateway's score decides: the exact lookup reports exactly 1.
    """
    if cache != 'HIT':
        return ''
    if matched_vid is not None:
        return 'standard' if matched_vid == vid else 'semantic'
    if score is not None:
        return 'standard' if score >= 0.999999 else 'semantic'
    return 'unknown'


def read_rows(run_dir: Path) -> list[dict]:
    path = run_dir / 'requests.csv'
    if not path.exists():
        return []
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


class Run:
    def __init__(
        self,
        run_id: str,
        workload: str,
        model: str,
        *,
        concurrency: int = 8,
        limit: int | None = None,
        expiry_s: int = DEFAULT_EXPIRY_S,
        threshold: float | None = None,
        settings: Settings | None = None,
    ):
        self.s = settings or Settings.load()
        self.dir = RUNS_DIR / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.workload = workload
        self.model = self.s.model_alias(model)
        self.concurrency = concurrency
        self.expiry_s = expiry_s
        self.threshold = threshold
        self.variants, stream, self.dataset = load_workload(workload)
        self.stream = stream[:limit] if limit else stream
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.meta = self._load_or_create_meta(limit)
        self.system = f'{SYSTEMS[workload]}\n\n[test-run {self.meta["nonce"]}]'

    def _load_or_create_meta(self, limit: int | None) -> dict:
        path = self.dir / 'meta.json'
        if path.exists():
            meta = json.loads(path.read_text())
            if meta['workload'] != self.workload or meta['model'] != self.model:
                raise SystemExit(
                    f'run {self.run_id} is {meta["workload"]} on {meta["model"]}; '
                    'use a new run id for a different workload or model'
                )
            meta['limit'] = limit or meta.get('limit')
            meta['concurrency'] = self.concurrency
            return meta
        return {
            'run_id': self.run_id,
            'workload': self.workload,
            'model': self.model,
            'embed_model': self.s.embed_model,
            'gateway_url': public_gateway_url(self.s.gateway_url),
            'cache_type': 'semantic',
            'requested_expiry_s': self.expiry_s,
            'requested_threshold': self.threshold,
            'applied_threshold': None,
            'applied_expiry': None,
            'concurrency': self.concurrency,
            'limit': limit,
            'nonce': secrets.token_hex(6),
            'created_at': now_iso(),
            'dataset': self.dataset,
        }

    def _save_meta(self) -> None:
        (self.dir / 'meta.json').write_text(json.dumps(self.meta, indent=2))

    # ------------------------------------------------------------------ run --

    def execute(self) -> dict:
        ensure_response_column(self.dir, force=True)
        done_rows = [r for r in read_rows(self.dir) if not r['error']]
        done = {int(r['seq']) for r in done_rows}
        # Rows that failed are retried on resume; keep only successful ones.
        if done_rows or (self.dir / 'requests.csv').exists():
            self._rewrite_csv(done_rows)
        self.id_to_seq = {r['response_id']: int(r['seq']) for r in done_rows
                          if r['cache'] == 'MISS' and r['response_id']}
        self.seq_vid = {int(r['seq']): r['vid'] for r in done_rows}
        self.counts = {
            'done': len(done_rows), 'hits': sum(r['cache'] == 'HIT' for r in done_rows),
            'standard': sum(r['hit_type'] == 'standard' for r in done_rows),
            'semantic': sum(r['hit_type'] == 'semantic' for r in done_rows), 'errors': 0,
        }
        self.shadow = PersistentShadowIndex(self.dir)
        todo = [row for row in self.stream if row['seq'] not in done]
        self.meta['status'] = 'running'
        self.meta.setdefault('started_at', now_iso())
        self.meta['resumed_at'] = now_iso() if done else None
        self._save_meta()
        self.t0 = time.time()
        self.done_at_start = len(done_rows)

        self.csv_f = (self.dir / 'requests.csv').open('a', newline='')
        self.csv_w = csv.DictWriter(self.csv_f, fieldnames=COLUMNS)
        if self.csv_f.tell() == 0:
            self.csv_w.writeheader()
        self.ans_f = (self.dir / 'answers.jsonl').open('a')
        self.gw = Gateway(self.s)

        def on_signal(_sig, _frame):
            self.stop.set()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, on_signal)
            signal.signal(signal.SIGINT, on_signal)

        it = iter(todo)
        it_lock = threading.Lock()

        def worker():
            while not self.stop.is_set():
                with it_lock:
                    row = next(it, None)
                if row is None:
                    return
                self._one(row)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(self.concurrency)]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            self._progress('running')
            time.sleep(2)
        for t in threads:
            t.join()

        self.csv_f.close()
        self.ans_f.close()
        self.shadow.close()
        self.gw.close()
        state = 'stopped' if self.stop.is_set() else 'complete'
        if state == 'complete' and self.counts['errors']:
            state = 'complete_with_errors'
        self.meta['status'] = state
        self.meta['finished_at'] = now_iso()
        self._save_meta()
        self._progress(state)
        return self.counts

    def _rewrite_csv(self, rows: list[dict]) -> None:
        write_rows(self.dir, rows)

    def _one(self, row: dict) -> None:
        seq, vid = row['seq'], row['vid']
        v = self.variants[vid]
        rec = {c: '' for c in COLUMNS}
        rec.update(seq=seq, started_at=now_iso(), vid=vid, group=v['group'], question=v['question'])
        try:
            emb = self.gw.embed(v['user_content'])
            score, match_seq = self.shadow.closest(emb.vector)
            self.shadow.log_request(seq, emb.vector)
            rec.update(
                embed_tokens=emb.tokens,
                embed_latency_ms=round(emb.latency_ms, 1),
                shadow_score='' if score is None else round(score, 6),
                shadow_match_seq='' if match_seq is None else match_seq,
            )
            messages = [
                {'role': 'system', 'content': self.system},
                {'role': 'user', 'content': v['user_content']},
            ]
            r = self.gw.chat(
                self.model, messages,
                expiry_s=self.expiry_s, threshold=self.threshold,
                max_tokens=self.dataset.get('max_tokens', 400),
            )
        except GatewayError as err:
            rec.update(error=str(err)[:500], http_status=err.status or '')
            with self.lock:
                self.counts['errors'] += 1
                self.csv_w.writerow(rec)
            return

        with self.lock:
            matched_seq = self.id_to_seq.get(r.response_id) if r.cache == 'HIT' else None
            matched_vid = self.seq_vid.get(matched_seq) if matched_seq else None
            if r.cache == 'MISS' and r.response_id:
                self.id_to_seq[r.response_id] = seq
            self.seq_vid[seq] = vid
        hit = r.cache == 'HIT'
        rec.update(
            cache=r.cache or 'NONE',
            hit_type=classify(r.cache, r.score, vid, matched_vid),
            gateway_score='' if r.score is None else r.score,
            matched_seq=matched_seq or '',
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            saved_input_tokens=r.prompt_tokens if hit else 0,
            saved_output_tokens=r.completion_tokens if hit else 0,
            billed_input_tokens=0 if hit else r.prompt_tokens,
            billed_output_tokens=0 if hit else r.completion_tokens,
            latency_ms=round(r.latency_ms, 1),
            attempts=r.attempts,
            http_status=r.status,
            response_id=r.response_id,
            response=r.answer,
        )
        # Exact hits never reach the embedding step in the gateway
        # (cache.go getSemanticChatCompletion tries the exact lookup first).
        if rec['hit_type'] == 'standard':
            rec['embed_tokens'] = 0
        if r.cache == 'MISS':
            self.shadow.add(seq, emb.vector)
        with self.lock:
            if self.meta['applied_threshold'] is None and r.applied_threshold:
                self.meta['applied_threshold'] = r.applied_threshold
                self.meta['applied_expiry'] = r.applied_expiry
                self.meta['first_response_headers'] = {
                    k: v for k, v in r.headers.items() if k in CACHE_HEADERS}
                self._save_meta()
            self.csv_w.writerow(rec)
            self.ans_f.write(json.dumps({'seq': seq, 'answer': r.answer}, ensure_ascii=False) + '\n')
            self.counts['done'] += 1
            self.counts['hits'] += hit
            if rec['hit_type'] in ('standard', 'semantic'):
                self.counts[rec['hit_type']] += 1

    def _progress(self, state: str) -> None:
        with self.lock:
            self.csv_f.flush() if not self.csv_f.closed else None
            self.ans_f.flush() if not self.ans_f.closed else None
            c = dict(self.counts)
        self.shadow.flush() if state == 'running' else None
        total = len(self.stream)
        elapsed = time.time() - self.t0
        new = c['done'] - self.done_at_start
        rate = new / elapsed if elapsed > 0 else 0
        p = {
            'run_id': self.run_id, 'state': state, 'total': total, **c,
            'hit_rate': round(c['hits'] / c['done'], 4) if c['done'] else 0,
            'req_per_s': round(rate, 2),
            'eta_s': round((total - c['done']) / rate) if rate > 0 else None,
            'updated_at': now_iso(),
        }
        tmp = self.dir / 'progress.json.tmp'
        tmp.write_text(json.dumps(p))
        tmp.replace(self.dir / 'progress.json')
