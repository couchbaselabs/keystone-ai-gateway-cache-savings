"""Wrong-hit review: does a semantic hit's answer fit the question actually asked?

Takes a seeded sample of semantic hits from a run and asks a judge model, with
caching off, whether the replayed answer fits the new question. Writes
judge.jsonl in the run directory. A person can override any verdict from the
UI (review.json); the report uses the human verdict when there is one.
"""

from __future__ import annotations

import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import RUNS_DIR, Settings
from .datasets import load_workload
from .gateway import Gateway
from .report import load_rows

JUDGE_PROMPT = """You are checking answers served from a semantic cache. The cache returned an \
answer that was originally written for a different but similar question. Decide whether that \
answer is an acceptable answer to the question that was actually asked.

Question actually asked:
<<<{question}>>>

Question the cached answer was written for:
<<<{matched_question}>>>

Answer that was returned:
<<<{answer}>>>

Verdicts:
- "fit": the answer addresses what was asked; a customer would be well served by it.
- "wrong": the answer addresses a different need, gives information that is incorrect for this \
question (for example a different order, amount, account type, product or procedure), or misses \
the point of the question.
- "unsure": you cannot tell.

Reply with JSON only: {{"verdict": "fit" | "wrong" | "unsure", "reason": "<one sentence>"}}"""


def run_judge(run_id: str, sample: int = 100, seed: int = 7, concurrency: int = 8) -> dict:
    run_dir = RUNS_DIR / run_id
    meta = json.loads((run_dir / 'meta.json').read_text())
    rows, _ = load_rows(run_dir)
    variants, _, _ = load_workload(meta['workload'])
    by_seq = {r['seq']: r for r in rows}
    sem = [r for r in rows if r['hit_type'] == 'semantic']
    rng = random.Random(seed)
    picked = sorted(rng.sample(sem, min(sample, len(sem))), key=lambda r: r['seq'])
    answers = _answers(run_dir)
    s = Settings.load()
    gw = Gateway(s)

    def judge(r: dict) -> dict:
        src = r['matched_seq'] or r['shadow_match_seq']
        matched = by_seq.get(src) if src else None
        matched_q = variants[matched['vid']]['question'] if matched else '(unknown)'
        q = variants[r['vid']]['question']
        prompt = JUDGE_PROMPT.format(question=q, matched_question=matched_q,
                                     answer=(r.get('response') or answers.get(r['seq'], ''))[:6000])
        out = {'seq': r['seq'], 'matched_seq': src, 'question': q, 'matched_question': matched_q,
               'score': r['gateway_score'], 'judge_model': s.judge_model}
        try:
            res = gw.chat(s.judge_model, [{'role': 'user', 'content': prompt}], cache='none',
                          max_tokens=200, debug=False)
            m = re.search(r'\{.*\}', res.answer, re.S)
            data = json.loads(m.group(0)) if m else {}
            verdict = data.get('verdict')
            out['verdict'] = verdict if verdict in ('fit', 'wrong', 'unsure') else 'unsure'
            out['reason'] = str(data.get('reason', ''))[:400]
        except Exception as err:  # noqa: BLE001 - record and keep judging the rest
            out['verdict'] = 'unsure'
            out['reason'] = f'judge error: {err}'[:400]
        return out

    with ThreadPoolExecutor(concurrency) as ex:
        results = list(ex.map(judge, picked))
    gw.close()
    with (run_dir / 'judge.jsonl').open('w') as f:
        for j in results:
            f.write(json.dumps(j, ensure_ascii=False) + '\n')
    wrong = sum(j['verdict'] == 'wrong' for j in results)
    return {'sampled': len(results), 'semantic_hits': len(sem), 'wrong': wrong}


def _answers(run_dir: Path) -> dict[int, str]:
    out = {}
    path = run_dir / 'answers.jsonl'
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                a = json.loads(line)
                out[a['seq']] = a['answer']
    return out
