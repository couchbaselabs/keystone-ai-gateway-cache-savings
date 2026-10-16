"""The per-request CSV: its columns, and the upgrade of runs written before a
column existed.

`response` holds the answer text the gateway returned (on a HIT, the cached
answer). Runs recorded before that column existed have their answers only in
answers.jsonl; `ensure_response_column` copies them into the CSV.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

COLUMNS = [
    'seq', 'started_at', 'vid', 'group', 'question',
    'cache', 'hit_type', 'gateway_score', 'shadow_score', 'shadow_match_seq',
    'matched_seq',
    'prompt_tokens', 'completion_tokens',
    'saved_input_tokens', 'saved_output_tokens',
    'billed_input_tokens', 'billed_output_tokens',
    'embed_tokens', 'latency_ms', 'embed_latency_ms',
    'attempts', 'http_status', 'error', 'response_id',
    'response',
]


def read_answers(run_dir: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    path = run_dir / 'answers.jsonl'
    if path.exists():
        with path.open() as f:
            for line in f:
                if line.strip():
                    a = json.loads(line)
                    out[int(a['seq'])] = a['answer']
    return out


def header(run_dir: Path) -> list[str]:
    path = run_dir / 'requests.csv'
    if not path.exists():
        return []
    with path.open(newline='') as f:
        return next(csv.reader(f), [])


def write_rows(run_dir: Path, rows: list[dict]) -> None:
    """Rewrite requests.csv sorted by seq, atomically (temp file, then rename)."""
    path = run_dir / 'requests.csv'
    tmp = run_dir / 'requests.csv.tmp'
    with tmp.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction='ignore')
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: int(r['seq'])))
    os.replace(tmp, path)


def run_active(run_dir: Path) -> bool:
    """True while the run's process is alive (it appends to the CSV)."""
    try:
        pid = json.loads((run_dir / 'job.json').read_text())['pid']
        os.kill(int(pid), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def ensure_response_column(run_dir: Path, *, force: bool = False) -> bool:
    """Add the `response` column to an older CSV. Returns True if it rewrote it.

    Skipped while the run is active, unless the caller is that run (force).
    """
    cols = header(run_dir)
    if not cols or 'response' in cols:
        return False
    if not force and run_active(run_dir):
        return False
    answers = read_answers(run_dir)
    with (run_dir / 'requests.csv').open(newline='') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r['response'] = '' if r.get('error') else answers.get(int(r['seq']), '')
    write_rows(run_dir, rows)
    return True
