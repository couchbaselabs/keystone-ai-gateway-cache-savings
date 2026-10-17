"""Settings: the cache demo's .env, overridden by the process environment.

The Node server passes its live preferences as environment variables when it
starts a CLI job, so edits made in the Preferences dialog apply without a
restart. Run from the shell, the CLI reads the same .env file directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The title every page, report and zip README uses.
TITLE = 'Serverless Model Service - Cache Savings Test Runs'

SAVINGS_DIR = Path(__file__).resolve().parent.parent
DEMO_DIR = SAVINGS_DIR.parent
DATA_DIR = Path(os.environ.get('SAVINGS_DATA_DIR', SAVINGS_DIR / 'data'))
RUNS_DIR = Path(os.environ.get('SAVINGS_RUNS_DIR', SAVINGS_DIR / 'runs'))

# List prices in USD per million tokens (input, output). From the test request:
# about $3/$15 for Sonnet 4.6 and $1/$5 for Haiku 4.5. Titan Text Embeddings
# v2 is $0.02 per million input tokens. Matched by substring of the model id.
PRICES = [
    ('sonnet', 3.0, 15.0),
    ('haiku', 1.0, 5.0),
    ('opus', 15.0, 75.0),
    ('titan-embed', 0.02, 0.0),
    ('mock-chat', 3.0, 15.0),
    ('mock-embed', 0.02, 0.0),
]


def price_for(model: str) -> tuple[float, float]:
    m = model.lower()
    for key, pin, pout in PRICES:
        if key in m:
            return pin, pout
    return 0.0, 0.0


def public_gateway_url(url: str) -> str:
    """The gateway URL as recorded in run files, which may be shared.

    Local gateways are kept; any other host is replaced, so a deployment's
    hostname never lands in a report or a public repository.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.hostname in ('localhost', '127.0.0.1', '::1'):
        return url
    return f'{parts.scheme or "https"}://<gateway host not recorded>'


# Response headers worth keeping in a run's meta.json: the cache settings the
# gateway applied. Request ids and model refs are left out.
CACHE_HEADERS = (
    'x-cache', 'x-cb-cache-enabled', 'x-cb-cache-expiry', 'x-cb-cache-type',
    'x-cb-semantic-cache-match-threshold',
)


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
            v = v[1:-1]
        out[k.strip()] = v
    return out


def env() -> dict[str, str]:
    path = Path(os.environ.get('ENV_FILE', DEMO_DIR / '.env'))
    merged = _read_env_file(path)
    merged.update({k: v for k, v in os.environ.items() if v is not None})
    return merged


@dataclass
class Settings:
    gateway_url: str
    api_key: str = field(repr=False)
    insecure_tls: bool
    embed_model: str
    sonnet_model: str
    haiku_model: str
    judge_model: str

    @classmethod
    def load(cls) -> 'Settings':
        e = env()
        haiku = e.get('HAIKU_MODEL') or e.get(
            'CHAT_MODEL', 'bedrock-global-anthropic-claude-haiku-4-5-20251001-v1-0'
        )
        sonnet = e.get('SONNET_MODEL', 'bedrock-global-anthropic-claude-sonnet-4-6')
        return cls(
            gateway_url=e.get('GATEWAY_URL', 'https://localhost:8081').rstrip('/'),
            api_key=e.get('GATEWAY_API_KEY', ''),
            insecure_tls=e.get('INSECURE_TLS', 'false') == 'true',
            embed_model=e.get('EMBED_MODEL', 'bedrock-amazon-titan-embed-text-v2-0'),
            sonnet_model=sonnet,
            haiku_model=haiku,
            judge_model=e.get('JUDGE_MODEL') or sonnet,
        )

    def model_alias(self, name: str) -> str:
        """`sonnet` / `haiku` resolve to the configured ids; anything else is an id."""
        return {'sonnet': self.sonnet_model, 'haiku': self.haiku_model}.get(name, name)
