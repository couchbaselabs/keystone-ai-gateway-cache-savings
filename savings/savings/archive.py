"""Zip one run for saving or sharing.

Two kinds, each with the run id as its top folder:

- share: what a reader needs (report, per-request CSV, wrong-hit sample and
  review, settings) plus a README explaining the files.
- full: the whole run folder, so it can be restored by unzipping into
  savings/runs/. Leaves out job.json (the process id of this machine) and,
  with vectors=False, the embedding logs (most of the size; needed only to
  replay other thresholds again or to resume).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .config import RUNS_DIR, TITLE

SHARE_FILES = ['report.html', 'report.md', 'requests.csv', 'judge.jsonl', 'review.json', 'spotcheck.json',
               'meta.json']
VECTOR_FILES = {'embeds.f32', 'embeds.seqs', 'shadow.f32', 'shadow.seqs'}
NEVER = {'job.json', 'requests.csv.tmp', 'progress.json.tmp'}

SHARE_README = """{title}
Run {run_id}
{workload} on {model}, {done:,} of {total:,} requests ({state}).

report.html   The report: hit rates (standard / semantic), tokens saved per hit and
              in total, embedding tokens, p50/p95 latency on hits and misses, and
              wrong hits at 1,000 / 10,000 / 20,000 requests, plus the hit rate at
              other similarity thresholds. Opens in any browser.
report.md     The same report as Markdown.
requests.csv  One row per request, the raw data for the pricing model. Key columns:
                question / response   what was asked and the answer returned
                cache            HIT or MISS
                hit_type         standard (exact) or semantic
                gateway_score    the gateway's similarity score on a hit
                shadow_score     closest cached match for every request
                saved_input_tokens / saved_output_tokens   tokens a hit saved
                                 (Bedrock's counts from when the answer was cached)
                billed_input_tokens / billed_output_tokens tokens a miss used
                embed_tokens     embedding tokens the semantic cache used
                latency_ms       client-measured latency
                matched_seq      the request whose cached answer a hit returned
judge.jsonl   The sampled semantic hits with the judge model's verdict (if judged).
review.json   Verdicts a person set on that sample, by request seq (if reviewed).
spotcheck.json  Manual spot-check flags (ok / wrong, with notes) by request seq, from
              filtered random samples; not part of the wrong-hit rate (if any).
meta.json     Settings: models, threshold and expiry applied, dataset, timings.
"""


def zip_name(run_id: str, kind: str, vectors: bool = True) -> str:
    suffix = 'share' if kind == 'share' else ('full' if vectors else 'full-no-vectors')
    return f'{run_id}-{suffix}.zip'


def build_zip(run_id: str, kind: str, out: Path, vectors: bool = True) -> Path:
    run_dir = RUNS_DIR / run_id
    if not (run_dir / 'meta.json').exists():
        raise SystemExit(f'no run {run_id}')
    if kind == 'share':
        files = [run_dir / f for f in SHARE_FILES if (run_dir / f).exists()]
    elif kind == 'full':
        files = sorted(
            p for p in run_dir.iterdir()
            if p.is_file() and p.name not in NEVER and (vectors or p.name not in VECTOR_FILES)
        )
    else:
        raise SystemExit(f'unknown kind {kind}; use share or full')

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.zip.tmp')
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as z:
        for p in files:
            # Raw float vectors do not compress; store them as they are.
            ctype = zipfile.ZIP_STORED if p.suffix == '.f32' else zipfile.ZIP_DEFLATED
            z.write(p, f'{run_id}/{p.name}', compress_type=ctype)
        if kind == 'share':
            meta = json.loads((run_dir / 'meta.json').read_text())
            prog = {}
            if (run_dir / 'progress.json').exists():
                prog = json.loads((run_dir / 'progress.json').read_text())
            z.writestr(f'{run_id}/README.txt', SHARE_README.format(
                title=TITLE, run_id=run_id, workload=meta.get('workload'), model=meta.get('model'),
                done=prog.get('done', 0), total=prog.get('total', 0),
                state=prog.get('state', meta.get('status', 'unknown'))))
    tmp.replace(out)
    return out
