"""Compaction Service — incremental delta compaction of event history into snapshots.

Triggered by:
  1. COMPACTION_NEEDED TerminalSignal (emitted by stream_processor when event delta
     >= EVENT_COMPACTION_THRESHOLD since the last snapshot).
  2. Stale context pack: latest pack older than STALE_PACK_AGE_HOURS.

Build pipeline per terminal (O(delta), not O(history)):
  1. Find latest snapshot — get its event_cursor and summary.
  2. Fetch events with id > last_cursor for this terminal (up to MAX_DELTA_EVENTS).
  3. Build rule-based delta summary (group by type, extract key excerpts).
  4. Merge with parent summary text.
  5. Persist new Snapshot row with updated event_cursor.
  6. Emit CompactionCompleted event.
  7. Deliver LLM compaction prompt to any registered overseer terminals so they can
     produce a richer narrative summary and update the snapshot.

Per-terminal cooldown of COMPACTION_COOLDOWN_SECS prevents runaway compaction loops.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy import func as sa_func
from sqlalchemy import select

from agentfactor.clients.database import ContextPack, EventLog, Snapshot, Terminal, session_scope
from agentfactor.models.enums import SignalType
from agentfactor.services.event_service import EventService

LOG = logging.getLogger(__name__)

STALE_PACK_AGE_HOURS = 24.0
COMPACTION_COOLDOWN_SECS = 1800.0
MAX_DELTA_EVENTS = 500


class CompactionService:
    """Background task: incremental snapshot compaction for agent terminals."""

    def __init__(
        self,
        event_service: EventService,
        inbox_service: Any,
    ) -> None:
        self._events = event_service
        self._inbox = inbox_service
        self._last_cursor: int = 0
        self._last_compaction: dict[str, float] = {}  # terminal_id → monotonic timestamp

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        import asyncio
        while True:
            try:
                self._tick()
            except Exception:
                LOG.exception("CompactionService tick failed")
            await asyncio.sleep(300)

    def _tick(self) -> None:
        self._scan_compaction_signals()
        self._check_stale_packs()

    def _scan_compaction_signals(self) -> None:
        """Watch for COMPACTION_NEEDED signals and trigger compaction per terminal."""
        import time

        new_events = self._events.get_recent(
            event_type="TerminalSignal",
            since_cursor=self._last_cursor,
            limit=200,
        )
        if not new_events:
            return
        self._last_cursor = max(e["id"] for e in new_events)

        now = time.monotonic()
        for event in new_events:
            payload = event.get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if payload.get("signal_type") != SignalType.COMPACTION_NEEDED.value:
                continue
            terminal_id = event.get("terminal_id")
            if not terminal_id:
                continue
            if now - self._last_compaction.get(terminal_id, 0.0) < COMPACTION_COOLDOWN_SECS:
                continue
            self.compact_terminal(terminal_id)
            self._last_compaction[terminal_id] = now

    def _check_stale_packs(self) -> None:
        """Trigger compaction for terminals whose latest context pack is stale."""
        import time

        stale_cutoff = datetime.utcnow() - timedelta(hours=STALE_PACK_AGE_HOURS)
        now = time.monotonic()

        try:
            with session_scope() as db:
                rows = db.execute(
                    select(
                        ContextPack.terminal_id,
                        sa_func.max(ContextPack.created_at).label("latest"),
                    ).group_by(ContextPack.terminal_id)
                ).all()
        except Exception:
            LOG.debug("CompactionService: stale pack query failed", exc_info=True)
            return

        for row in rows:
            terminal_id = row.terminal_id
            try:
                latest_dt = datetime.fromisoformat(str(row.latest).split(".")[0])
            except (ValueError, AttributeError):
                continue
            if latest_dt >= stale_cutoff:
                continue
            if now - self._last_compaction.get(terminal_id, 0.0) < COMPACTION_COOLDOWN_SECS:
                continue
            self.compact_terminal(terminal_id)
            self._last_compaction[terminal_id] = now

    # ------------------------------------------------------------------
    # Core compaction (also callable directly via API endpoint)
    # ------------------------------------------------------------------

    def compact_terminal(self, terminal_id: str) -> Optional[dict]:
        """Run one compaction cycle; returns new snapshot dict or None if nothing to do."""
        import time

        LOG.info("Compacting terminal %s", terminal_id)

        try:
            with session_scope() as db:
                snap = db.execute(
                    select(Snapshot)
                    .where(Snapshot.created_by == terminal_id)
                    .order_by(desc(Snapshot.created_at))
                    .limit(1)
                ).scalar_one_or_none()
                last_cursor = snap.event_cursor if snap else 0
                parent_id = snap.id if snap else None
                parent_summary = snap.summary_text if snap else ""
                parent_state: dict = json.loads(snap.derived_state) if snap and snap.derived_state else {}

            with session_scope() as db:
                delta_rows = db.execute(
                    select(EventLog)
                    .where(
                        EventLog.id > last_cursor,
                        EventLog.terminal_id == terminal_id,
                    )
                    .order_by(EventLog.id)
                    .limit(MAX_DELTA_EVENTS)
                ).scalars().all()
                delta_events = [
                    {
                        "id": e.id,
                        "type": e.type,
                        "timestamp": str(e.timestamp),
                        "payload": e.payload,
                    }
                    for e in delta_rows
                ]
        except Exception:
            LOG.exception("CompactionService: error fetching data for terminal %s", terminal_id)
            return None

        if not delta_events:
            LOG.debug("No delta events for terminal %s — skipping", terminal_id)
            return None

        new_cursor = max(e["id"] for e in delta_events)
        delta_summary = _build_delta_summary(delta_events)
        merged_summary = _merge_summaries(parent_summary, delta_summary)
        derived_state = _build_derived_state(parent_state, delta_events)

        snap_id = f"snap_{uuid4().hex[:12]}"
        with session_scope() as db:
            db.add(Snapshot(
                id=snap_id,
                parent_id=parent_id,
                event_cursor=new_cursor,
                summary_text=merged_summary,
                derived_state=json.dumps(derived_state),
                created_by=terminal_id,
                is_pinned=False,
            ))

        self._events.emit(
            type="CompactionCompleted",
            payload={
                "snapshot_id": snap_id,
                "terminal_id": terminal_id,
                "delta_event_count": len(delta_events),
                "new_cursor": new_cursor,
                "parent_id": parent_id,
            },
            terminal_id=terminal_id,
        )

        self._deliver_llm_prompt(terminal_id, snap_id, delta_events, merged_summary)
        self._last_compaction[terminal_id] = time.monotonic()

        LOG.info(
            "Compaction done for %s: snapshot=%s delta=%d cursor=%d",
            terminal_id, snap_id, len(delta_events), new_cursor,
        )
        return {
            "snapshot_id": snap_id,
            "terminal_id": terminal_id,
            "parent_id": parent_id,
            "event_cursor": new_cursor,
            "delta_event_count": len(delta_events),
            "summary_text": merged_summary,
            "derived_state": derived_state,
            "llm_prompt": self.build_llm_prompt(terminal_id, delta_events, merged_summary),
        }

    # ------------------------------------------------------------------
    # LLM prompt builder
    # ------------------------------------------------------------------

    def build_llm_prompt(
        self, terminal_id: str, delta_events: list[dict], auto_summary: str
    ) -> str:
        """Return a structured prompt an LLM overseer uses to write a richer summary."""
        event_list = "\n".join(
            f"  - [{e['type']}] {_truncate(e.get('payload', ''), 120)}"
            for e in delta_events[:30]
        )
        if len(delta_events) > 30:
            event_list += f"\n  ... ({len(delta_events) - 30} more events omitted)"
        return (
            f"[COMPACTION_REQUEST] terminal={terminal_id}\n"
            f"Delta events ({len(delta_events)} total):\n{event_list}\n\n"
            f"Auto-generated summary:\n{auto_summary}\n\n"
            "Task: Rewrite the above as a concise narrative (≤200 words) covering: "
            "key decisions, work completed, blockers encountered, and current state. "
            "Focus on *what changed* since the last summary. "
            "Reply with ONLY the narrative — no JSON, no preamble."
        )

    def _deliver_llm_prompt(
        self,
        terminal_id: str,
        snap_id: str,
        delta_events: list[dict],
        auto_summary: str,
    ) -> None:
        prompt = self.build_llm_prompt(terminal_id, delta_events, auto_summary)
        try:
            with session_scope() as db:
                overseers = db.execute(
                    select(Terminal).where(Terminal.agent_profile.like("%overseer%"))
                ).scalars().all()
                overseer_ids = [o.id for o in overseers]
        except Exception:
            LOG.debug("Could not find overseer terminals for compaction prompt", exc_info=True)
            return
        for oid in overseer_ids:
            try:
                self._inbox.queue_message(
                    sender_id="system",
                    receiver_id=oid,
                    message=prompt,
                    dedupe=False,
                )
            except Exception:
                LOG.debug("Could not deliver compaction prompt to overseer %s", oid, exc_info=True)

    # ------------------------------------------------------------------
    # Read access (used by API endpoints)
    # ------------------------------------------------------------------

    def get_compaction_history(self, terminal_id: Optional[str] = None) -> list[dict]:
        """Return snapshot list, newest first; optionally filtered by terminal_id."""
        with session_scope() as db:
            q = select(Snapshot).order_by(desc(Snapshot.created_at)).limit(100)
            if terminal_id:
                q = q.where(Snapshot.created_by == terminal_id)
            rows = db.execute(q).scalars().all()
            return [_snapshot_to_dict(r) for r in rows]

    def diff_snapshots(self, snap_id_a: str, snap_id_b: str) -> dict:
        """Return a structured diff between two snapshots."""
        with session_scope() as db:
            a = db.get(Snapshot, snap_id_a)
            b = db.get(Snapshot, snap_id_b)

        missing = snap_id_a if a is None else (snap_id_b if b is None else None)
        if missing:
            raise ValueError(f"Snapshot not found: {missing}")

        state_a: dict = json.loads(a.derived_state) if a.derived_state else {}
        state_b: dict = json.loads(b.derived_state) if b.derived_state else {}

        return {
            "snapshot_a": snap_id_a,
            "snapshot_b": snap_id_b,
            "cursor_delta": b.event_cursor - a.event_cursor,
            "summary_a": a.summary_text[:500],
            "summary_b": b.summary_text[:500],
            "state_diff": _diff_dicts(state_a, state_b),
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_KEY_EVENT_TYPES = {
    "DECISION", "CompactionCompleted", "WorkItemProofReceived",
    "WorkItemStatusChanged", "ContextLossRecoveryTriggered", "TopologyProposalCreated",
}


def _build_delta_summary(events: list[dict]) -> str:
    type_counts = Counter(e["type"] for e in events)
    lines = [f"[DELTA: {len(events)} events]"]
    for ev_type, count in type_counts.most_common(10):
        lines.append(f"  {ev_type}: {count}")

    key_excerpts: list[str] = []
    for e in events:
        if e["type"] in _KEY_EVENT_TYPES:
            payload = e.get("payload", "")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    pass
            excerpt = _truncate(str(payload), 150)
            key_excerpts.append(f"  [{e['type']}] {excerpt}")

    if key_excerpts:
        lines.append("Key events:")
        lines.extend(key_excerpts[:10])

    return "\n".join(lines)


def _merge_summaries(parent: str, delta: str) -> str:
    if not parent:
        return delta
    return f"{parent}\n\n--- UPDATE ---\n{delta}"


def _build_derived_state(parent_state: dict, events: list[dict]) -> dict:
    state = dict(parent_state)
    type_counts = Counter(e["type"] for e in events)

    sig: dict = state.get("signal_summary", {})
    for k, v in type_counts.items():
        sig[k] = sig.get(k, 0) + v
    state["signal_summary"] = sig
    state["total_events_processed"] = state.get("total_events_processed", 0) + len(events)
    state["last_compaction_at"] = datetime.utcnow().isoformat()

    decisions: list[dict] = state.get("key_decisions", [])
    for e in events:
        if e["type"] == "DECISION":
            payload = e.get("payload", "")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    pass
            excerpt = (
                payload.get("excerpt", str(payload))[:200]
                if isinstance(payload, dict)
                else str(payload)[:200]
            )
            decisions.append({"at": e["timestamp"], "excerpt": excerpt})
    state["key_decisions"] = decisions[-20:]

    return state


def _snapshot_to_dict(row: Snapshot) -> dict:
    return {
        "id": row.id,
        "parent_id": row.parent_id,
        "terminal_id": row.created_by,
        "event_cursor": row.event_cursor,
        "summary_text": row.summary_text,
        "derived_state": json.loads(row.derived_state) if row.derived_state else {},
        "created_at": str(row.created_at),
        "is_pinned": row.is_pinned,
    }


def _diff_dicts(a: dict, b: dict) -> dict:
    all_keys = set(a) | set(b)
    return {
        "added": {k: b[k] for k in b if k not in a},
        "removed": {k: a[k] for k in a if k not in b},
        "changed": {
            k: {"from": a[k], "to": b[k]}
            for k in all_keys
            if k in a and k in b and a[k] != b[k]
        },
    }


def _truncate(value: Any, max_len: int) -> str:
    return str(value)[:max_len]
