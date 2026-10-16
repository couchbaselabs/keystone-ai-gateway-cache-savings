"""Client for the gateway's OpenAI-compatible API.

Header names and behaviour come from ai-gateway (pkg/common/constants.go,
pkg/handlers/types/chat_completion.go):

- `X-Cache: HIT|MISS` is set whenever a cache lookup completed.
- `X-cb-match-score` is set only on a HIT and only with `X-cb-debug: true`.
  Semantic mode tries the exact lookup first, which reports score 1, so a
  score of 1 is a standard (exact) hit and anything lower a semantic hit.
- A HIT replays the stored response unchanged: same `id`, same `usage`, i.e.
  the token counts Bedrock reported when the answer was first cached.
- `X-cb-cache-expiry` and `X-cb-semantic-cache-match-threshold` (debug only)
  report the settings the gateway actually applied.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings

H_CACHE = 'X-cb-cache'
H_THRESHOLD = 'X-cb-cache-threshold'
H_EXPIRY = 'X-cb-cache-expiry-duration'
H_DEBUG = 'X-cb-debug'

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class GatewayError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class ChatResult:
    status: int
    latency_ms: float
    cache: str  # HIT | MISS | '' when the gateway sent no X-Cache header
    score: float | None
    applied_threshold: str
    applied_expiry: str
    response_id: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    attempts: int
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class EmbedResult:
    vector: list[float]
    tokens: int
    latency_ms: float


class Gateway:
    def __init__(self, settings: Settings, timeout: float = 180.0, max_retries: int = 6):
        self.s = settings
        self.max_retries = max_retries
        self.client = httpx.Client(
            base_url=settings.gateway_url,
            headers={'Authorization': f'Bearer {settings.api_key}'},
            verify=not settings.insecure_tls,
            timeout=timeout,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=64),
        )

    def close(self) -> None:
        self.client.close()

    def _post(self, path: str, body: dict[str, Any], headers: dict[str, str] | None = None):
        """POST with retries on throttling and transient errors.

        Returns (response, latency of the successful attempt in ms, attempts).
        """
        delay = 1.0
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                r = self.client.post(path, json=body, headers=headers or {})
            except httpx.HTTPError as err:
                last = GatewayError(f'{path}: {type(err).__name__}: {err}')
            else:
                ms = (time.perf_counter() - t0) * 1000
                if r.status_code < 400:
                    return r, ms, attempt
                last = GatewayError(f'{path} returned {r.status_code}: {r.text[:300]}', r.status_code)
                if r.status_code not in RETRY_STATUS:
                    raise last
            time.sleep(delay + random.random() * delay)
            delay = min(delay * 2, 30.0)
        assert last is not None
        raise last

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        cache: str = 'semantic',
        expiry_s: int | None = None,
        threshold: float | None = None,
        max_tokens: int = 400,
        debug: bool = True,
    ) -> ChatResult:
        headers = {H_CACHE: cache}
        if debug:
            headers[H_DEBUG] = 'true'
        if expiry_s is not None:
            headers[H_EXPIRY] = str(expiry_s)
        if threshold is not None:
            headers[H_THRESHOLD] = str(threshold)
        body = {'model': model, 'messages': messages, 'temperature': 0, 'max_tokens': max_tokens}
        r, ms, attempts = self._post('/v1/chat/completions', body, headers)
        data = r.json()
        usage = data.get('usage') or {}
        score = r.headers.get('x-cb-match-score')
        choices = data.get('choices') or [{}]
        return ChatResult(
            status=r.status_code,
            latency_ms=ms,
            cache=r.headers.get('x-cache', ''),
            score=float(score) if score not in (None, '') else None,
            applied_threshold=r.headers.get('x-cb-semantic-cache-match-threshold', ''),
            applied_expiry=r.headers.get('x-cb-cache-expiry', ''),
            response_id=str(data.get('id', '')),
            answer=(choices[0].get('message') or {}).get('content') or '',
            prompt_tokens=int(usage.get('prompt_tokens') or 0),
            completion_tokens=int(usage.get('completion_tokens') or 0),
            attempts=attempts,
            headers={k: v for k, v in r.headers.items() if k == 'x-cache' or k.startswith('x-cb-')},
        )

    def embed(self, text: str) -> EmbedResult:
        r, ms, _ = self._post('/v1/embeddings', {'model': self.s.embed_model, 'input': text})
        data = r.json()
        try:
            vec = data['data'][0]['embedding']
        except (KeyError, IndexError, TypeError) as err:
            raise GatewayError(f'embedding response has no data[0].embedding: {err}') from err
        usage = data.get('usage') or {}
        return EmbedResult(vec, int(usage.get('prompt_tokens') or 0), ms)
