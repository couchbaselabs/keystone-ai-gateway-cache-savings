"""Client-side copy of the semantic cache's vector index.

The gateway reports a match score only on a HIT, but the test asks for the
closest cached match of every request, so other thresholds can be evaluated
without rerunning. The runner embeds each request's last user message through
the gateway's /v1/embeddings with the cache's embedding model (the same text
and model the gateway embeds; ai-gateway chat_completion.go GetEmbedding), and
adds the vector here whenever the gateway stores an entry (a MISS). For Titan
the gateway stores the query embedding itself, so these are the same vectors
the gateway searches.

Differences from the gateway, which the report states: this search is exact
where the gateway's vector index is approximate, and an entry counts as stored
as soon as its MISS response arrives, while the gateway's index picks it up a
little later.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np


class ShadowIndex:
    def __init__(self, dims: int | None = None):
        self.lock = threading.Lock()
        self.dims = dims
        self.vecs = np.zeros((0, dims or 1), dtype=np.float32)
        self.seqs: list[int] = []
        self.n = 0

    @staticmethod
    def _unit(vec) -> np.ndarray:
        v = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def closest(self, vec) -> tuple[float | None, int | None]:
        """Best cosine similarity among stored entries and the seq that stored it."""
        q = self._unit(vec)
        with self.lock:
            if self.n == 0:
                return None, None
            sims = self.vecs[: self.n] @ q
            i = int(np.argmax(sims))
            return float(sims[i]), self.seqs[i]

    def add(self, seq: int, vec) -> None:
        v = self._unit(vec)
        with self.lock:
            if self.n == 0:
                self.dims = v.shape[0]
                self.vecs = np.zeros((1024, self.dims), dtype=np.float32)
            if self.n == len(self.vecs):
                self.vecs = np.concatenate([self.vecs, np.zeros_like(self.vecs)])
            self.vecs[self.n] = v
            self.seqs.append(seq)
            self.n += 1


class VectorLog:
    """Append-only (seq, vector) log on disk: <stem>.f32 and <stem>.seqs."""

    def __init__(self, run_dir: Path, stem: str):
        self.vec_path = run_dir / f'{stem}.f32'
        self.seq_path = run_dir / f'{stem}.seqs'
        self.lock = threading.Lock()
        self._vf = self.vec_path.open('ab')
        self._sf = self.seq_path.open('a')

    @staticmethod
    def read(run_dir: Path, stem: str) -> tuple[list[int], np.ndarray]:
        vec_path, seq_path = run_dir / f'{stem}.f32', run_dir / f'{stem}.seqs'
        if not (vec_path.exists() and seq_path.exists()):
            return [], np.zeros((0, 0), dtype=np.float32)
        seqs = [int(x) for x in seq_path.read_text().split()]
        raw = np.fromfile(vec_path, dtype=np.float32)
        if not seqs:
            return [], np.zeros((0, 0), dtype=np.float32)
        dims = raw.size // len(seqs)
        return seqs, raw[: dims * len(seqs)].reshape(len(seqs), dims)

    def append(self, seq: int, unit_vec: np.ndarray) -> None:
        with self.lock:
            self._vf.write(np.asarray(unit_vec, dtype=np.float32).tobytes())
            self._sf.write(f'{seq}\n')

    def flush(self) -> None:
        with self.lock:
            self._vf.flush()
            self._sf.flush()

    def close(self) -> None:
        with self.lock:
            self._vf.close()
            self._sf.close()


class PersistentShadowIndex(ShadowIndex):
    """ShadowIndex of stored entries, plus a log of every request's embedding.

    Both survive a stop, so a run can resume. The full log lets the report
    replay the cache at other thresholds (report.simulate_thresholds).
    """

    def __init__(self, run_dir: Path):
        super().__init__()
        seqs, vecs = VectorLog.read(run_dir, 'shadow')
        for seq, v in zip(seqs, vecs):
            super().add(seq, v)
        self.stored = VectorLog(run_dir, 'shadow')
        self.requests = VectorLog(run_dir, 'embeds')

    def log_request(self, seq: int, vec) -> None:
        self.requests.append(seq, self._unit(vec))

    def add(self, seq: int, vec) -> None:
        super().add(seq, vec)
        self.stored.append(seq, self._unit(vec))

    def flush(self) -> None:
        self.stored.flush()
        self.requests.flush()

    def close(self) -> None:
        self.stored.close()
        self.requests.close()
