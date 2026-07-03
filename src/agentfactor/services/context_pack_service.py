"""Context pack service — builds semantic context packs for agent terminals.

A context pack is a structured bundle of work items, events, and decisions
relevant to a terminal's current task. It is built on-demand and can be
delivered to agents via the inbox system.

Build pipeline:
  gather candidates → detect contradictions → deduplicate →
  annotate staleness → greedy token pack → primacy/recency layout →
  score → persist → return
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import desc, select

from agentfactor.clients.database import ContextPack, EventLog, WorkItem, session_scope
from agentfactor.models.enums import WorkItemStatus
from agentfactor.services.embedding_service import EmbeddingService, _cosine, _hash_embed
from agentfactor.services.event_service import EventService

LOG = logging.getLogger(__name__)

STALENESS_DAYS = 14
DEDUP_THRESHOLD = 0.88
TOKEN_BUDGET_DEFAULT = 8000
CHARS_PER_TOKEN = 4

_SECTION_WEIGHTS = {
    "critical": 0.35,
    "decisions": 0.20,
    "history": 0.30,
    "peers": 0.10,
    "meta": 0.05,
}


class ContextPackService:
    """Build, validate, and persist context packs for agent terminals."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        event_service: EventService,
    ) -> None:
        self._embedder = embedding_service
        self._events = event_service

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------

    def build_context_pack(
        self,
        terminal_id: str,
        query: str,
        token_budget: int = TOKEN_BUDGET_DEFAULT,
    ) -> dict[str, Any]:
        """Build a full context pack for `terminal_id` using `query` for semantic retrieval."""
        work_items = self._get_relevant_work_items(terminal_id, query)
        events = self._get_relevant_events(terminal_id, query)

        all_items: list[dict] = work_items + events
        contradictions = self.detect_contradictions(all_items)

        work_items = self.deduplicate_pack(work_items)
        events = self.deduplicate_pack(events)

        work_items = self.annotate_staleness(work_items)
        events = self.annotate_staleness(events)

        packed = self._greedy_pack(work_items=work_items, events=events, token_budget=token_budget)
        layout = self.layout_context_pack(packed)
        layout["contradictions"] = contradictions
        layout["quality_score"] = self.score_pack(layout)

        tokens_used = _estimate_tokens(json.dumps(layout))
        pack_id = self._persist_pack(
            terminal_id=terminal_id,
            sections=layout,
            quality_score=layout["quality_score"],
            token_budget=token_budget,
            tokens_used=tokens_used,
        )
        layout["pack_id"] = pack_id
        return layout

    def build_differential_pack(
        self,
        terminal_id: str,
        base_pack_id: str,
        query: str,
    ) -> dict[str, Any]:
        """Build a diff pack: only items new or changed since the base pack."""
        with session_scope() as db:
            base = db.get(ContextPack, base_pack_id)
            if base is None:
                return self.build_context_pack(terminal_id, query)
            try:
                base_sections = json.loads(base.sections)
            except json.JSONDecodeError:
                base_sections = {}

        base_item_ids: set[str] = set()
        for section_items in base_sections.values():
            if isinstance(section_items, list):
                for item in section_items:
                    if isinstance(item, dict) and "id" in item:
                        base_item_ids.add(str(item["id"]))

        full = self.build_context_pack(terminal_id, query)

        diff_sections: dict[str, Any] = {}
        for section, items in full.items():
            if not isinstance(items, list):
                diff_sections[section] = items
                continue
            new_items = [i for i in items if str(i.get("id", "")) not in base_item_ids]
            if new_items:
                diff_sections[section] = new_items

        tokens_used = _estimate_tokens(json.dumps(diff_sections))
        pack_id = self._persist_pack(
            terminal_id=terminal_id,
            sections=diff_sections,
            quality_score=full.get("quality_score", 0.0),
            token_budget=TOKEN_BUDGET_DEFAULT,
            tokens_used=tokens_used,
            is_differential=True,
            base_pack_id=base_pack_id,
        )
        diff_sections["pack_id"] = pack_id
        diff_sections["base_pack_id"] = base_pack_id
        diff_sections["is_differential"] = True
        return diff_sections

    def get_latest_pack(self, terminal_id: str) -> Optional[dict[str, Any]]:
        """Return the most recently stored context pack for `terminal_id`."""
        with session_scope() as db:
            row = db.execute(
                select(ContextPack)
                .where(ContextPack.terminal_id == terminal_id)
                .order_by(desc(ContextPack.created_at))
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            try:
                sections = json.loads(row.sections)
            except json.JSONDecodeError:
                sections = {}
            return {
                "pack_id": row.id,
                "terminal_id": row.terminal_id,
                "quality_score": row.quality_score,
                "token_budget": row.token_budget,
                "tokens_used": row.tokens_used,
                "is_differential": row.is_differential,
                "base_pack_id": row.base_pack_id,
                "created_at": str(row.created_at),
                **sections,
            }

    # ------------------------------------------------------------------
    # Component functions (also callable individually / for testing)
    # ------------------------------------------------------------------

    def detect_contradictions(self, items: list[dict]) -> list[dict]:
        """Return a list of potential contradictions found among `items`."""
        contradictions: list[dict] = []

        # Decision conflicts: two DECISION events with similar-but-not-identical content
        decisions = [i for i in items if i.get("type") == "DECISION"]
        for i, d1 in enumerate(decisions):
            for d2 in decisions[i + 1:]:
                text1 = str(d1.get("payload", ""))
                text2 = str(d2.get("payload", ""))
                sim = _cosine(_hash_embed(text1), _hash_embed(text2))
                # Same topic but meaningfully different conclusion → potential conflict
                if 0.35 < sim < 0.72:
                    contradictions.append({
                        "type": "decision_conflict",
                        "item_a_id": d1.get("id"),
                        "item_b_id": d2.get("id"),
                        "similarity": round(sim, 3),
                    })

        # Unverified completions: work items stuck in NEEDS_VERIFICATION
        work_items = [i for i in items if i.get("_item_kind") == "work_item"]
        for wi in work_items:
            if wi.get("status") == WorkItemStatus.NEEDS_VERIFICATION.value:
                contradictions.append({
                    "type": "awaiting_proof",
                    "work_item_id": wi.get("id"),
                    "work_item_title": wi.get("title"),
                    "note": "Work item awaiting proof collection.",
                })

        return contradictions

    def deduplicate_pack(self, items: list[dict]) -> list[dict]:
        """Remove near-duplicate items (cosine similarity >= DEDUP_THRESHOLD)."""
        if len(items) <= 1:
            return items
        kept: list[dict] = []
        kept_vecs: list[list[float]] = []
        for item in items:
            text = (
                item.get("_text")
                or str(item.get("payload", ""))
                or str(item.get("title", ""))
            )
            vec = _hash_embed(text)
            if not any(_cosine(vec, kv) >= DEDUP_THRESHOLD for kv in kept_vecs):
                kept.append(item)
                kept_vecs.append(vec)
        return kept

    def annotate_staleness(self, items: list[dict]) -> list[dict]:
        """Add `_stale=True` to items older than STALENESS_DAYS days."""
        cutoff = datetime.utcnow() - timedelta(days=STALENESS_DAYS)
        for item in items:
            ts = item.get("timestamp") or item.get("created_at") or item.get("updated_at")
            if ts:
                try:
                    dt = datetime.fromisoformat(str(ts).split(".")[0])
                    item["_stale"] = dt < cutoff
                except (ValueError, AttributeError):
                    pass
        return items

    def layout_context_pack(self, packed: dict[str, list[dict]]) -> dict[str, Any]:
        """Arrange sections for primacy/recency attention layout."""
        return {
            "critical": packed.get("critical", []),    # primacy — seen first
            "decisions": packed.get("decisions", []),  # middle — reference material
            "history": packed.get("history", []),      # recency — freshest at end
            "peers": packed.get("peers", []),
            "meta": packed.get("meta", []),
        }

    def score_pack(self, pack: dict[str, Any]) -> float:
        """Compute pack quality score in [0.0, 1.0]."""
        critical = pack.get("critical", [])
        history = pack.get("history", [])
        decisions = pack.get("decisions", [])
        contradictions = pack.get("contradictions", [])

        score = 0.0
        if critical:
            score += 0.30
        if history:
            non_stale = [e for e in history if not e.get("_stale", False)]
            score += 0.30 * min(len(non_stale) / max(len(history), 1), 1.0)
        if decisions:
            score += 0.20
        all_items = critical + history + decisions
        if all_items:
            fresh_ratio = sum(1 for i in all_items if not i.get("_stale", False)) / len(all_items)
            score += 0.20 * fresh_ratio
        contradiction_penalty = min(len(contradictions) * 0.1, 0.30)
        return round(max(0.0, min(score - contradiction_penalty, 1.0)), 3)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_relevant_work_items(self, terminal_id: str, query: str) -> list[dict]:
        """Fetch work items owned by this terminal plus semantically similar ones."""
        items: list[dict] = []

        with session_scope() as db:
            owned = db.execute(
                select(WorkItem).where(
                    WorkItem.owner_terminal_id == terminal_id,
                    WorkItem.status.notin_([
                        WorkItemStatus.DONE.value,
                        WorkItemStatus.CANCELLED.value,
                    ]),
                )
            ).scalars().all()
            for wi in owned:
                items.append(_work_item_to_dict(wi, relevance_boost=1.0))

        owned_ids = {i["id"] for i in items}
        semantic = self._embedder.vector_search_work_items(query, top_k=5)
        for s in semantic:
            if s["id"] not in owned_ids:
                with session_scope() as db:
                    wi = db.get(WorkItem, s["id"])
                    if wi:
                        items.append(_work_item_to_dict(wi, relevance_boost=s["score"]))

        return items

    def _get_relevant_events(self, terminal_id: str, query: str) -> list[dict]:
        """Fetch recent + semantically relevant events for this terminal."""
        recent = self._events.get_recent(terminal_id=terminal_id, limit=50)

        semantic = self._embedder.vector_search_events(query, top_k=20, terminal_id=terminal_id)
        semantic_ids = {e["id"] for e in semantic}

        all_events: list[dict] = []
        seen_ids: set[int] = set()

        for e in semantic:
            all_events.append({**e, "_text": f"{e.get('type', '')} {e.get('payload', '')}"})
            seen_ids.add(e["id"])

        for e in recent:
            if e["id"] not in seen_ids:
                all_events.append({
                    **e,
                    "_text": f"{e.get('type', '')} {e.get('payload', '')}",
                    "score": 0.5,
                })

        return all_events

    def _greedy_pack(
        self,
        work_items: list[dict],
        events: list[dict],
        token_budget: int,
    ) -> dict[str, list[dict]]:
        """Fill sections greedily within token budget allocation."""
        budgets = {s: int(token_budget * w) for s, w in _SECTION_WEIGHTS.items()}

        critical: list[dict] = []
        used_critical = 0
        for wi in work_items:
            tokens = _estimate_tokens(json.dumps(wi))
            if used_critical + tokens <= budgets["critical"]:
                critical.append(wi)
                used_critical += tokens

        decisions: list[dict] = []
        used_decisions = 0
        for e in events:
            if e.get("type") != "DECISION":
                continue
            tokens = _estimate_tokens(json.dumps(e))
            if used_decisions + tokens <= budgets["decisions"]:
                decisions.append(e)
                used_decisions += tokens

        history: list[dict] = []
        used_history = 0
        other_events = sorted(
            [e for e in events if e.get("type") != "DECISION"],
            key=lambda x: x.get("timestamp", ""),
            reverse=True,
        )
        for e in other_events:
            tokens = _estimate_tokens(json.dumps(e))
            if used_history + tokens <= budgets["history"]:
                history.append(e)
                used_history += tokens

        return {
            "critical": critical,
            "decisions": decisions,
            "history": history,
            "peers": [],
            "meta": [],
        }

    def _persist_pack(
        self,
        terminal_id: str,
        sections: dict[str, Any],
        quality_score: float,
        token_budget: int,
        tokens_used: int,
        is_differential: bool = False,
        base_pack_id: Optional[str] = None,
    ) -> str:
        pack_id = f"pack_{uuid4().hex[:12]}"
        with session_scope() as db:
            db.add(ContextPack(
                id=pack_id,
                terminal_id=terminal_id,
                sections=json.dumps(sections),
                quality_score=quality_score,
                token_budget=token_budget,
                tokens_used=tokens_used,
                is_differential=is_differential,
                base_pack_id=base_pack_id,
            ))
        return pack_id


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _work_item_to_dict(wi: WorkItem, relevance_boost: float = 1.0) -> dict[str, Any]:
    return {
        "_item_kind": "work_item",
        "id": wi.id,
        "title": wi.title,
        "description": wi.description,
        "status": str(wi.status.value if hasattr(wi.status, "value") else wi.status),
        "type": str(wi.type.value if hasattr(wi.type, "value") else wi.type),
        "priority": wi.priority,
        "acceptance_criteria": json.loads(wi.acceptance_criteria or "[]"),
        "relevance_boost": relevance_boost,
        "_text": f"{wi.title} {wi.description}",
        "created_at": str(wi.created_at),
        "updated_at": str(wi.updated_at),
    }


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)
