"""Embedding service — text vectorization and cosine-similarity vector search.

Generates 128-dimensional embeddings via character n-gram + word unigram hashing
(no external ML library required). Vectors are stored as packed floats (512 bytes)
in the `embedding` BLOB column of WorkItem and EventLog.

Vector search is O(n) in Python — adequate for the scale of this system
(hundreds to low thousands of items per project).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
from typing import Optional

from sqlalchemy import select

from agentfactor.clients.database import EventLog, WorkItem, session_scope

LOG = logging.getLogger(__name__)

DIMS = 128
_STRUCT_FMT = f"<{DIMS}f"  # little-endian 32-bit floats


class EmbeddingService:
    """Generate and store text embeddings; run cosine-similarity search against DB rows."""

    # ------------------------------------------------------------------
    # Core embedding
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Return a normalized DIMS-dimensional vector for `text`."""
        return _hash_embed(text)

    # ------------------------------------------------------------------
    # Persist helpers
    # ------------------------------------------------------------------

    def embed_and_store_work_item(self, item_id: str) -> None:
        """Compute and persist embedding for a work item (no-op if not found)."""
        with session_scope() as db:
            item = db.get(WorkItem, item_id)
            if item is None:
                return
            vec = self.embed(f"{item.title} {item.description}")
            item.embedding = _pack(vec)

    def embed_and_store_event(self, event_id: int) -> None:
        """Compute and persist embedding for an event log entry."""
        with session_scope() as db:
            event = db.get(EventLog, event_id)
            if event is None:
                return
            try:
                payload_obj = json.loads(event.payload or "{}")
                text = " ".join(str(v) for v in payload_obj.values() if isinstance(v, str))
            except (json.JSONDecodeError, AttributeError):
                text = (event.payload or "")[:500]
            vec = self.embed(f"{event.type} {text}")
            event.embedding = _pack(vec)

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------

    def vector_search_work_items(
        self,
        query: str,
        project_id: Optional[str] = None,
        top_k: int = 10,
        min_score: float = 0.05,
    ) -> list[dict]:
        """Return work items most similar to `query`, ranked by cosine similarity."""
        query_vec = self.embed(query)
        with session_scope() as db:
            q = select(WorkItem)
            if project_id:
                q = q.where(WorkItem.project_id == project_id)
            rows = db.execute(q).scalars().all()

        scored: list[tuple[float, dict]] = []
        for row in rows:
            vec = _unpack(row.embedding)
            if vec is None:
                vec = self.embed(f"{row.title} {row.description}")
            score = _cosine(query_vec, vec)
            if score >= min_score:
                scored.append((score, {
                    "id": row.id,
                    "title": row.title,
                    "project_id": row.project_id,
                    "score": round(score, 4),
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def vector_search_events(
        self,
        query: str,
        top_k: int = 20,
        terminal_id: Optional[str] = None,
        event_type: Optional[str] = None,
        min_score: float = 0.05,
    ) -> list[dict]:
        """Return events most similar to `query`, ranked by cosine similarity.

        Searches the most recent 500 events to keep latency bounded.
        """
        query_vec = self.embed(query)
        with session_scope() as db:
            q = select(EventLog)
            if terminal_id:
                q = q.where(EventLog.terminal_id == terminal_id)
            if event_type:
                q = q.where(EventLog.type == event_type)
            q = q.order_by(EventLog.id.desc()).limit(500)
            rows = db.execute(q).scalars().all()

        scored: list[tuple[float, dict]] = []
        for row in rows:
            vec = _unpack(row.embedding)
            if vec is None:
                try:
                    payload_obj = json.loads(row.payload or "{}")
                    text = " ".join(str(v) for v in payload_obj.values() if isinstance(v, str))
                except (json.JSONDecodeError, AttributeError):
                    text = (row.payload or "")[:500]
                vec = self.embed(f"{row.type} {text}")
            score = _cosine(query_vec, vec)
            if score >= min_score:
                scored.append((score, {
                    "id": row.id,
                    "type": row.type,
                    "terminal_id": row.terminal_id,
                    "timestamp": str(row.timestamp),
                    "payload": row.payload,
                    "score": round(score, 4),
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]


# ---------------------------------------------------------------------------
# Module-level helpers (also imported by context_pack_service)
# ---------------------------------------------------------------------------


def _hash_embed(text: str) -> list[float]:
    """Character n-gram + word unigram hashing into a DIMS-dimensional normalized vector."""
    text = text.lower()[:2000]
    vec = [0.0] * DIMS

    for n in (2, 3):
        for i in range(len(text) - n + 1):
            ngram = text[i: i + n]
            h = int(hashlib.sha1(ngram.encode()).hexdigest(), 16)
            vec[h % DIMS] += 1.0

    for word in text.split():
        h = int(hashlib.sha1(word.encode()).hexdigest(), 16)
        vec[h % DIMS] += 2.0  # words weighted 2× vs character n-grams

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two already-normalized vectors."""
    return sum(x * y for x, y in zip(a, b))


def _pack(vec: list[float]) -> bytes:
    return struct.pack(_STRUCT_FMT, *vec)


def _unpack(blob: Optional[bytes]) -> Optional[list[float]]:
    if blob is None or len(blob) != DIMS * 4:
        return None
    return list(struct.unpack(_STRUCT_FMT, blob))
