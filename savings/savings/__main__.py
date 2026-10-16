"""Command line: python -m savings <command> (run from the savings/ directory).

  datasets  --workload chatbot|rag      build the request stream
  run       --workload W --model M      send the stream through the gateway
  judge     --run ID                    judge a sample of semantic hits
  report    --run ID                    build report.{json,md,html} for one run
  summary   --runs ID,ID,...            one report across several runs
  status    --run ID                    print progress
  check                                 one request to confirm settings and headers
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import datasets, report
from .config import RUNS_DIR, Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='python -m savings', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    d = sub.add_parser('datasets', help='build a request stream')
    d.add_argument('--workload', choices=['chatbot', 'rag'], required=True)
    d.add_argument('--n', type=int, default=20000)
    d.add_argument('--seed', type=int, default=42)
    d.add_argument('--groups', type=int, default=1500, help='RAG: base questions to generate')
    d.add_argument('--qgen-model', default='', help='RAG: model that writes the questions')

    r = sub.add_parser('run', help='send the stream through the gateway')
    r.add_argument('--workload', choices=['chatbot', 'rag'], required=True)
    r.add_argument('--model', required=True, help='sonnet, haiku or a gateway model id')
    r.add_argument('--run-id', default='')
    r.add_argument('--concurrency', type=int, default=8)
    r.add_argument('--limit', type=int, default=0, help='first N requests only (smoke runs)')
    r.add_argument('--expiry', type=int, default=172800, help='cache expiry in seconds')
    r.add_argument('--threshold', type=float, default=None,
                   help='override the similarity threshold (default: the gateway default)')
    r.add_argument('--no-report', action='store_true')

    j = sub.add_parser('judge', help='judge a sample of semantic hits')
    j.add_argument('--run', required=True)
    j.add_argument('--sample', type=int, default=100)
    j.add_argument('--seed', type=int, default=7)

    rp = sub.add_parser('report', help='build the report for one run')
    rp.add_argument('--run', required=True)
    rp.add_argument('--no-replay', action='store_true', help='skip the threshold replay')

    sm = sub.add_parser('summary', help='one report across runs')
    sm.add_argument('--runs', required=True, help='comma-separated run ids')

    st = sub.add_parser('status', help='print progress of a run')
    st.add_argument('--run', required=True)

    ck = sub.add_parser('check', help='one request to confirm the settings and headers')
    ck.add_argument('--model', default='haiku')

    a = ap.parse_args(argv)

    if a.cmd == 'datasets':
        if a.workload == 'chatbot':
            meta = datasets.build_chatbot(n=a.n, seed=a.seed)
        else:
            meta = datasets.build_rag(n=a.n, seed=a.seed, n_groups=a.groups, qgen_model=a.qgen_model)
        print(json.dumps(meta['stats'], indent=2))
    elif a.cmd == 'run':
        from .runner import Run

        run_id = a.run_id or f'{a.workload}-{a.model}-{datetime.now():%Y%m%d-%H%M}'
        run = Run(run_id, a.workload, a.model, concurrency=a.concurrency,
                  limit=a.limit or None, expiry_s=a.expiry, threshold=a.threshold)
        print(f'run {run_id}: {len(run.stream)} requests to {run.model} '
              f'via {run.s.gateway_url}', file=sys.stderr)
        counts = run.execute()
        print(json.dumps(counts))
        if not a.no_report:
            report.build(run_id)
            print(f'report: {RUNS_DIR / run_id / "report.html"}', file=sys.stderr)
    elif a.cmd == 'judge':
        from .judge import run_judge

        print(json.dumps(run_judge(a.run, sample=a.sample, seed=a.seed)))
        report.build(a.run)
    elif a.cmd == 'report':
        rep = report.build(a.run, simulate=not a.no_replay)
        print(report.render_md([rep]))
    elif a.cmd == 'summary':
        out = report.build_summary([x.strip() for x in a.runs.split(',') if x.strip()])
        print(out / 'summary.html')
    elif a.cmd == 'status':
        p = RUNS_DIR / a.run / 'progress.json'
        print(p.read_text() if p.exists() else '{}')
    elif a.cmd == 'check':
        return check(a.model)
    return 0


def check(model: str) -> int:
    """Two identical requests: the second must be a HIT with debug headers."""
    import secrets

    from .gateway import Gateway

    s = Settings.load()
    gw = Gateway(s)
    m = s.model_alias(model)
    msgs = [{'role': 'system', 'content': f'Answer in one sentence. [check {secrets.token_hex(4)}]'},
            {'role': 'user', 'content': 'What is a semantic cache?'}]
    e = gw.embed('What is a semantic cache?')
    a = gw.chat(m, msgs, max_tokens=60)
    b = gw.chat(m, msgs, max_tokens=60)
    gw.close()
    out = {
        'gateway': s.gateway_url, 'model': m, 'embed_model': s.embed_model,
        'embed_dims': len(e.vector), 'embed_tokens': e.tokens,
        'first': {'cache': a.cache, 'usage': [a.prompt_tokens, a.completion_tokens],
                  'ms': round(a.latency_ms), 'headers': a.headers},
        'second': {'cache': b.cache, 'score': b.score, 'same_id': a.response_id == b.response_id,
                   'usage': [b.prompt_tokens, b.completion_tokens], 'ms': round(b.latency_ms)},
    }
    print(json.dumps(out, indent=2))
    ok = a.cache == 'MISS' and b.cache == 'HIT' and a.response_id == b.response_id
    print('OK' if ok else 'UNEXPECTED: expected MISS then HIT with the same id', file=sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
