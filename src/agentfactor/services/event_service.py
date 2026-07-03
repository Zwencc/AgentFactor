"""Append-only event log service.

All state changes in the context-v2 system flow through emit().
Never call UPDATE or DELETE on the event_log table directly.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import desc, select

from agentfactor.clients.database import EventLog, Snapshot, session_scope

LOG = logging.getLogger(__name__)


class EventService:
    """Write to and query the immutable event log."""

    def emit(
        self,
        type: str,
        payload: dict[str, Any],
        terminal_id: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> int:
        """Append one event and return its id (monotonic cursor position)."""
        with session_scope() as db:
            event = EventLog(
                terminal_id=terminal_id,
                type=type,
                payload=json.dumps(payload, default=str),
                source_id=source_id or terminal_id,
            )
            db.add(event)
            db.flush()
            db.refresh(event)
            return event.id

    def get_recent(
        self,
        terminal_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
        since_cursor: int = 0,
    ) -> list[dict[str, Any]]:
        """Return events as dicts, newest first."""
        with session_scope() as db:
            q = select(EventLog).where(EventLog.id > since_cursor)
            if terminal_id:
                q = q.where(EventLog.terminal_id == terminal_id)
            if event_type:
                q = q.where(EventLog.type == event_type)
            q = q.order_by(desc(EventLog.id)).limit(limit)
            rows = db.execute(q).scalars().all()
            return [self._row_to_dict(r) for r in rows]

    def latest_cursor(self) -> int:
        """Return the id of the most recent event, or 0 if the log is empty."""
        with session_scope() as db:
            row = db.execute(select(EventLog.id).order_by(desc(EventLog.id)).limit(1)).scalar()
            return row or 0

    def get_latest_snapshot(self) -> Optional[dict[str, Any]]:
        """Return the most recent (non-pinned-preferred) snapshot, or None."""
        with session_scope() as db:
            row = db.execute(
                select(Snapshot).order_by(desc(Snapshot.event_cursor)).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "parent_id": row.parent_id,
                "event_cursor": row.event_cursor,
                "summary_text": row.summary_text,
                "created_by": row.created_by,
                "created_at": str(row.created_at),
                "is_pinned": row.is_pinned,
            }

    @staticmethod
    def _row_to_dict(row: EventLog) -> dict[str, Any]:
        try:
            payload = json.loads(row.payload)
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": row.payload}
        return {
            "id": row.id,
            "terminal_id": row.terminal_id,
            "type": row.type,
            "payload": payload,
            "source_id": row.source_id,
            "timestamp": str(row.timestamp),
        }
