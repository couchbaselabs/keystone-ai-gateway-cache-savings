"""Builds the two request streams.

A stream is two files under data/:

- `<workload>-variants.jsonl`: one line per distinct user message, with the
  question, its group (canonical question) and the full user message content.
- `<workload>-stream.jsonl`: the ordered request sequence, `{seq, vid}`.

Workload A, chatbot: Bitext customer-support utterances (27 intents, about 1,000
phrasings each, already reworded and with typos). Popularity is Zipf-Mandelbrot
over intents and over phrasings within an intent, so popular questions repeat
exactly and the rest arrive as rewordings, as in a real support bot.

Workload B, RAG: questions generated over Couchbase Capella docs pages, each with
three rewordings. Every request carries 3,000-5,200 tokens of BM25-retrieved
documentation in the user message, ahead of the question.
"""

from __future__ import annotations

import csv
import html
import json
import math
import random
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

import httpx
import numpy as np

from .config import DATA_DIR

BITEXT_URL = (
    'https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset'
    '/resolve/main/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv'
)
DOCS_SITEMAP = 'https://docs.couchbase.com/sitemap-cloud.xml'

CHATBOT_SYSTEM = """You are Nova, the customer support assistant for Northwind Outfitters, an online \
retailer of outdoor clothing and gear that ships to the United States, Canada and the European Union.

Store policies you can rely on:
- Orders can be cancelled free of charge until they ship, usually within 24 hours of purchase. \
After shipping, the customer must request a return instead.
- Returns are accepted within 30 days of delivery for unused items in original packaging. \
Refunds go back to the original payment method within 5-7 business days after the return is received.
- Standard shipping takes 3-5 business days, express 1-2 business days. Orders over $75 ship free.
- Accepted payment methods: Visa, Mastercard, American Express, PayPal, Apple Pay and store gift cards.
- Invoices are available under Account > Orders and are emailed when an order ships.
- Customers can change the shipping address until the order ships, from Account > Orders.
- Account emails, passwords and newsletter preferences are managed under Account > Settings.
- Human agents are available by chat or phone at 1-800-555-0199, Monday to Friday, 8am-8pm ET.

How to answer:
- Be warm, direct and specific. Give step-by-step instructions when the customer has to do something.
- Never invent order details, tracking numbers or amounts. If you need an order number or other \
details you don't have, say exactly what the customer should provide or where to find it.
- Do not promise exceptions to these policies; offer to connect the customer with a human agent instead.
- Answer in 120 to 200 words. Use short paragraphs or a short numbered list, no headings."""

RAG_SYSTEM = """You are the Couchbase Capella documentation assistant. You answer questions from \
developers and database administrators using only the documentation excerpts provided in the user \
message.

Rules:
- Base every statement on the excerpts. If they do not contain the answer, say so plainly and \
suggest what to search for in the docs instead. Never invent settings, limits, commands or UI paths.
- Prefer concrete steps, exact UI labels, CLI commands and parameter names when the excerpts contain them.
- When several excerpts disagree, prefer the most specific one and mention the difference.
- Answer in 250 to 350 words. Use a short numbered list for procedures and inline code for names, \
no headings."""

RAG_USER_TEMPLATE = (
    'Documentation excerpts:\n\n{context}\n\n'
    'Using only the excerpts above, answer this question.\n\nQuestion: {question}'
)

# Placeholder values. Small pools keep some exact repeats ("track order 10452")
# while still varying the text the way real customers do.
PLACEHOLDERS = {
    '{{Order Number}}': ['10452', '10987', '11230', '11871', '12045', '12399', '12816', '13377'],
    '{{Account Type}}': ['Premium', 'Standard', 'Business', 'Free', 'Gold'],
    '{{Person Name}}': ['Maria', 'James', 'Priya', 'Chen', 'Alex', 'Fatima'],
    '{{Account Category}}': ['Premium', 'Standard', 'Business', 'Family'],
    '{{Refund Amount}}': ['$25', '$49.99', '$120', '$89', '$15.50'],
    '{{Currency Symbol}}': ['$', '€', 'CA$'],
    '{{Delivery City}}': ['Denver', 'Toronto', 'Seattle', 'Berlin', 'Austin'],
    '{{Delivery Country}}': ['the US', 'Canada', 'Germany', 'France'],
    '{{Invoice Number}}': ['INV-20431', 'INV-20977', 'INV-21510'],
}


def est_tokens(text: str) -> int:
    """Rough token estimate (about 4 characters per token for English)."""
    return max(1, len(text) // 4)


def zipf_mandelbrot(n: int, s: float, q: float) -> np.ndarray:
    ranks = np.arange(1, n + 1, dtype=float)
    w = 1.0 / np.power(ranks + q, s)
    return w / w.sum()


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def read_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def stream_stats(stream: list[dict], variants: dict[str, dict]) -> dict:
    """How much repetition a stream has, before any cache is involved."""
    seen_v: set[str] = set()
    seen_g: set[str] = set()
    exact = reworded = 0
    for row in stream:
        v = variants[row['vid']]
        if row['vid'] in seen_v:
            exact += 1
        elif v['group'] in seen_g:
            reworded += 1
        seen_v.add(row['vid'])
        seen_g.add(v['group'])
    n = len(stream)
    toks = [variants[r['vid']]['user_tokens'] for r in stream]
    return {
        'requests': n,
        'distinct_messages': len(seen_v),
        'distinct_groups': len(seen_g),
        'exact_repeat_share': round(exact / n, 4) if n else 0,
        'reworded_repeat_share': round(reworded / n, 4) if n else 0,
        'user_tokens_est_mean': round(float(np.mean(toks)), 1) if toks else 0,
    }


# ----------------------------------------------------------------- chatbot ---


def load_bitext() -> list[dict]:
    path = DATA_DIR / 'bitext.csv'
    if not path.exists():
        log(f'downloading Bitext dataset to {path}')
        path.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream('GET', BITEXT_URL, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with path.open('wb') as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def fill_placeholders(text: str, rng: random.Random) -> str:
    for ph, pool in PLACEHOLDERS.items():
        while ph in text:
            text = text.replace(ph, rng.choice(pool), 1)
    return re.sub(r'\{\{[^}]+\}\}', 'it', text)


def build_chatbot(
    n: int = 20000,
    seed: int = 42,
    intent_s: float = 1.0,
    intent_q: float = 2.0,
    phrase_s: float = 1.1,
    phrase_q: float = 5.0,
) -> dict:
    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)
    rows = load_bitext()
    by_intent: dict[str, list[str]] = {}
    for r in rows:
        by_intent.setdefault(r['intent'], []).append(r['instruction'].strip())
    intents = sorted(by_intent)
    rng.shuffle(intents)  # popularity order of intents
    for k in intents:
        uniq = sorted(set(by_intent[k]))
        rng.shuffle(uniq)  # popularity order of phrasings within the intent
        by_intent[k] = uniq
    p_intent = zipf_mandelbrot(len(intents), intent_s, intent_q)
    p_phrase = {k: zipf_mandelbrot(len(v), phrase_s, phrase_q) for k, v in by_intent.items()}

    variants: dict[str, dict] = {}
    by_text: dict[str, str] = {}
    stream = []
    intent_draws = nprng.choice(len(intents), size=n, p=p_intent)
    for seq, ii in enumerate(intent_draws, start=1):
        intent = intents[ii]
        pi = int(nprng.choice(len(by_intent[intent]), p=p_phrase[intent]))
        text = fill_placeholders(by_intent[intent][pi], rng)
        vid = by_text.get(text)
        if vid is None:
            vid = f'c{len(by_text):05d}'
            by_text[text] = vid
            variants[vid] = {
                'vid': vid,
                'group': intent,
                'question': text,
                'user_content': text,
                'user_tokens': est_tokens(text),
            }
        stream.append({'seq': seq, 'vid': vid})

    meta = {
        'workload': 'chatbot',
        'source': 'Bitext customer support (27K, v11)',
        'seed': seed,
        'n': n,
        'popularity': {
            'intent': f'Zipf-Mandelbrot s={intent_s} q={intent_q} over {len(intents)} intents',
            'phrase': f'Zipf-Mandelbrot s={phrase_s} q={phrase_q} over phrasings within an intent',
        },
        'system_prompt': CHATBOT_SYSTEM,
        'system_tokens_est': est_tokens(CHATBOT_SYSTEM),
        'max_tokens': 400,
        'stats': stream_stats(stream, variants),
    }
    save_workload('chatbot', variants, stream, meta)
    return meta


# --------------------------------------------------------------------- RAG ---


class _ArticleText(HTMLParser):
    """Collects visible text inside <article class="doc">, keeping block breaks."""

    BLOCK = {'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'pre', 'tr', 'dt', 'dd', 'div', 'td'}
    SKIP = {'script', 'style', 'nav', 'svg', 'button'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0  # depth inside the article
        self.skip = 0
        self.parts: list[str] = []
        self.title = ''
        self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth == 0:
            if tag == 'article' and 'doc' in (a.get('class') or '').split():
                self.depth = 1
            return
        self.depth += 1
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append('\n')
        if tag == 'h1':
            self._in_h1 = True

    def handle_endtag(self, tag):
        if self.depth == 0:
            return
        self.depth -= 1
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == 'h1':
            self._in_h1 = False

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.parts.append(data)
            if self._in_h1:
                self.title += data

    def text(self) -> str:
        t = html.unescape(''.join(self.parts))
        t = re.sub(r'[ \t]+', ' ', t)
        t = re.sub(r'\n\s*\n+', '\n\n', t)
        return t.strip()


def fetch_docs(max_pages: int = 500) -> list[dict]:
    path = DATA_DIR / 'capella-docs.jsonl'
    if path.exists():
        return read_jsonl(path)
    sm = httpx.get(DOCS_SITEMAP, timeout=60, follow_redirects=True)
    sm.raise_for_status()
    urls = re.findall(r'<loc>([^<]+)</loc>', sm.text)[:max_pages]
    log(f'fetching {len(urls)} Capella docs pages')

    def get(url: str) -> dict | None:
        try:
            r = httpx.get(url, timeout=60, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as err:
            log(f'  skip {url}: {err}')
            return None
        p = _ArticleText()
        p.feed(r.text)
        text = p.text()
        if len(text) < 400:
            return None
        return {'url': url, 'title': p.title.strip() or url.rsplit('/', 1)[-1], 'text': text}

    with ThreadPoolExecutor(8) as ex:
        pages = [p for p in ex.map(get, urls) if p]
    write_jsonl(path, pages)
    return pages


def chunk_pages(pages: list[dict], target_tokens: int = 450) -> list[dict]:
    chunks = []
    for pi, page in enumerate(pages):
        paras = [p.strip() for p in page['text'].split('\n\n') if p.strip()]
        buf: list[str] = []
        size = 0
        for para in paras:
            t = est_tokens(para)
            if buf and size + t > target_tokens:
                chunks.append((pi, '\n\n'.join(buf)))
                buf, size = [], 0
            buf.append(para[: target_tokens * 8])
            size += min(t, target_tokens * 2)
        if buf:
            chunks.append((pi, '\n\n'.join(buf)))
    out = []
    for i, (pi, text) in enumerate(chunks):
        if est_tokens(text) < 60:
            continue
        out.append({
            'cid': f'k{i:05d}',
            'page': pi,
            'title': pages[pi]['title'],
            'url': pages[pi]['url'],
            'text': text,
            'tokens': est_tokens(text),
        })
    return out


_WORD = re.compile(r'[a-z0-9_]+')
_STOP = set(
    'a an the and or of to in on for with is are be by as at it this that from how what do does '
    'can i my we you your which when where why should into using use about'.split()
)


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d)) for d in docs]
        self.lens = np.array([sum(tf.values()) for tf in self.tfs], dtype=float)
        self.avg = float(self.lens.mean()) if len(self.lens) else 1.0
        df: Counter = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.index: dict[str, list[int]] = {}
        for i, tf in enumerate(self.tfs):
            for w in tf:
                self.index.setdefault(w, []).append(i)

    def search(self, query: str, k: int = 20) -> list[int]:
        scores: dict[int, float] = {}
        for w in set(tokenize(query)):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i in self.index[w]:
                f = self.tfs[i][w]
                denom = f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg)
                scores[i] = scores.get(i, 0.0) + idf * f * (self.k1 + 1) / denom
        return [i for i, _ in sorted(scores.items(), key=lambda x: -x[1])[:k]]


QGEN_PROMPT = """Below is an excerpt from the Couchbase Capella documentation, page "{title}".

{text}

Write one realistic question a developer or DBA might ask that this excerpt answers, then three \
rewordings of the same question as different users would type it (vary vocabulary, length and \
formality; one can be terse, one can have a small typo). Reply with JSON only:
{{"question": "...", "rewordings": ["...", "...", "..."]}}"""


def generate_questions(chunks: list[dict], n_groups: int, model: str, seed: int) -> list[dict]:
    """One base question plus three rewordings per sampled chunk, via the gateway."""
    from .config import Settings
    from .gateway import Gateway

    # Every attempted chunk is recorded, failures as {"cid", "failed": true}, so a
    # rebuild from a saved questions file makes no model calls and reproduces
    # the same stream.
    path = DATA_DIR / 'rag-questions.jsonl'
    attempted = read_jsonl(path) if path.exists() else []
    done = {g['cid'] for g in attempted}
    have = [g for g in attempted if not g.get('failed')]
    rng = random.Random(seed)
    pool = [c for c in chunks if c['tokens'] >= 150]
    rng.shuffle(pool)
    todo = [c for c in pool[: n_groups] if c['cid'] not in done]
    if not todo:
        return have[:n_groups], 0
    log(f'generating questions for {len(todo)} chunks with {model} (cache off)')
    gw = Gateway(Settings.load())

    def gen(c: dict) -> dict | None:
        prompt = QGEN_PROMPT.format(title=c['title'], text=c['text'][:4000])
        try:
            r = gw.chat(model, [{'role': 'user', 'content': prompt}], cache='none',
                        max_tokens=300, debug=False)
            m = re.search(r'\{.*\}', r.answer, re.S)
            data = json.loads(m.group(0)) if m else {}
            qs = [data.get('question', '')] + list(data.get('rewordings', []))[:3]
            qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()]
        except Exception as err:  # noqa: BLE001 - skip a bad chunk, keep going
            log(f'  skip {c["cid"]}: {err}')
            return None
        if len(qs) < 2:
            return None
        return {'cid': c['cid'], 'title': c['title'], 'url': c['url'], 'questions': qs}

    with path.open('a') as f, ThreadPoolExecutor(8) as ex:
        for i, (c, g) in enumerate(zip(todo, ex.map(gen, todo)), 1):
            f.write(json.dumps(g or {'cid': c['cid'], 'failed': True}, ensure_ascii=False) + '\n')
            f.flush()
            if g:
                have.append(g)
            if i % 100 == 0:
                log(f'  {i}/{len(todo)}')
    gw.close()
    return have[:n_groups], len(todo)


def build_context(question: str, bm25: BM25, chunks: list[dict], budget: int) -> tuple[str, list[str]]:
    parts, ids, used = [], [], 0
    for i in bm25.search(question, k=30):
        c = chunks[i]
        if used + c['tokens'] > budget and used > 0:
            continue
        parts.append(f'[{len(parts) + 1}] {c["title"]} ({c["url"]})\n{c["text"]}')
        ids.append(c['cid'])
        used += c['tokens']
        if used >= budget * 0.92:
            break
    return '\n\n---\n\n'.join(parts), ids


def build_rag(
    n: int = 20000,
    seed: int = 42,
    n_groups: int = 1500,
    group_s: float = 1.0,
    group_q: float = 20.0,
    qgen_model: str = '',
) -> dict:
    from .config import Settings

    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)
    pages = fetch_docs()
    chunks = chunk_pages(pages)
    log(f'{len(pages)} pages, {len(chunks)} chunks')
    model = qgen_model or Settings.load().haiku_model
    prev_meta = DATA_DIR / 'rag-meta.json'
    prev_model = json.loads(prev_meta.read_text()).get('question_model') if prev_meta.exists() else None
    groups, generated = generate_questions(chunks, n_groups, model, seed)
    if not generated and prev_model:
        model = prev_model  # every question came from the saved file
    bm25 = BM25([f'{c["title"]}\n{c["text"]}' for c in chunks])

    variants: dict[str, dict] = {}
    gvids: list[list[str]] = []
    for gi, g in enumerate(groups):
        vids = []
        for qi, q in enumerate(g['questions']):
            budget = rng.randint(3000, 5200)
            context, ids = build_context(q, bm25, chunks, budget)
            content = RAG_USER_TEMPLATE.format(context=context, question=q)
            vid = f'r{gi:04d}-{qi}'
            variants[vid] = {
                'vid': vid,
                'group': g['cid'],
                'question': q,
                'source_url': g['url'],
                'context_chunks': ids,
                'user_content': content,
                'user_tokens': est_tokens(content),
            }
            vids.append(vid)
        gvids.append(vids)
    order = list(range(len(gvids)))
    rng.shuffle(order)  # popularity order of groups
    p_group = zipf_mandelbrot(len(order), group_s, group_q)
    stream = []
    for seq, gi in enumerate(nprng.choice(len(order), size=n, p=p_group), start=1):
        vids = gvids[order[gi]]
        stream.append({'seq': seq, 'vid': vids[int(nprng.integers(len(vids)))]})

    meta = {
        'workload': 'rag',
        'source': f'Couchbase Capella docs ({DOCS_SITEMAP}), {len(pages)} pages, {len(chunks)} chunks',
        'seed': seed,
        'n': n,
        'question_model': model,
        'groups': len(groups),
        'popularity': {
            'group': f'Zipf-Mandelbrot s={group_s} q={group_q} over {len(groups)} base questions',
            'variant': 'uniform over the base question and its 3 rewordings',
        },
        'retrieval': 'BM25 over 450-token chunks; 3,000-5,200 context tokens per message',
        'system_prompt': RAG_SYSTEM,
        'system_tokens_est': est_tokens(RAG_SYSTEM),
        'max_tokens': 700,
        'stats': stream_stats(stream, variants),
    }
    save_workload('rag', variants, stream, meta)
    return meta


# ------------------------------------------------------------------ common ---


def save_workload(name: str, variants: dict[str, dict], stream: list[dict], meta: dict) -> None:
    write_jsonl(DATA_DIR / f'{name}-variants.jsonl', variants.values())
    write_jsonl(DATA_DIR / f'{name}-stream.jsonl', stream)
    (DATA_DIR / f'{name}-meta.json').write_text(json.dumps(meta, indent=2))
    log(json.dumps(meta['stats'], indent=2))


def load_workload(name: str) -> tuple[dict[str, dict], list[dict], dict]:
    meta_path = DATA_DIR / f'{name}-meta.json'
    if not meta_path.exists():
        raise SystemExit(f'no {name} dataset; run: python -m savings datasets --workload {name}')
    variants = {v['vid']: v for v in read_jsonl(DATA_DIR / f'{name}-variants.jsonl')}
    stream = read_jsonl(DATA_DIR / f'{name}-stream.jsonl')
    return variants, stream, json.loads(meta_path.read_text())
