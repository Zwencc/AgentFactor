"""Cleanup utilities for stale terminals and logs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from agentfactor import constants
from agentfactor.clients.database import InboxMessage, Terminal as TerminalORM, session_scope
from agentfactor.models.enums import InboxStatus, TerminalStatus
from agentfactor.services.terminal_service import TerminalService

LOG = logging.getLogger(__name__)


class CleanupService:
    """Removes stale resources."""

    def __init__(
        self,
        terminal_service: TerminalService,
        retention_days: int = 7,
        idle_timeout_minutes: int = 30,
    ) -> None:
        self.terminals = terminal_service
        self.retention = timedelta(days=retention_days)
        self.idle_timeout = timedelta(minutes=idle_timeout_minutes)
        # terminal_id → datetime when we first observed it in an idle-eligible state
        self._first_seen_idle: dict[str, datetime] = {}

    def purge_completed_terminals(self) -> None:
        cutoff = datetime.now(timezone.utc) - self.retention
        with session_scope() as db:
            terminals = (
                db.query(TerminalORM)
                .filter(
                    TerminalORM.status.in_(
                        [TerminalStatus.COMPLETED, TerminalStatus.ERROR]
                    ),
                    TerminalORM.created_at <= cutoff,
                )
                .all()
            )
        for terminal in terminals:
            self.terminals.delete_terminal(terminal.id)

    def purge_idle_terminals(self) -> None:
        """Close terminals that have been COMPLETED/ERROR with no pending work for idle_timeout."""
        now = datetime.now(timezone.utc)

        with session_scope() as db:
            all_terminals = db.query(TerminalORM).all()
            idle_candidates = [
                t for t in all_terminals
                if t.status in (TerminalStatus.COMPLETED, TerminalStatus.ERROR)
            ]
            if not idle_candidates:
                self._first_seen_idle.clear()
                return

            # Terminals that still have pending/in-flight inbox messages
            terminals_with_pending: set[str] = {
                row[0]
                for row in db.query(InboxMessage.receiver_id)
                .filter(InboxMessage.status.in_([InboxStatus.PENDING, InboxStatus.IN_FLIGHT]))
                .distinct()
                .all()
            }

            # Sessions that still have at least one non-idle terminal (for supervisor guard)
            session_has_active: set[str] = {
                t.session_name
                for t in all_terminals
                if t.status not in (TerminalStatus.COMPLETED, TerminalStatus.ERROR)
            }

        to_close: list[str] = []
        candidate_ids = {t.id for t in idle_candidates}

        for terminal in idle_candidates:
            # Has undelivered messages → reset clock, don't touch
            if terminal.id in terminals_with_pending:
                self._first_seen_idle.pop(terminal.id, None)
                continue

            # Supervisor: keep alive while any worker in the session is still active
            if terminal.window_name.startswith("supervisor-"):
                if terminal.session_name in session_has_active:
                    self._first_seen_idle.pop(terminal.id, None)
                    continue

            first_seen = self._first_seen_idle.setdefault(terminal.id, now)
            if now - first_seen >= self.idle_timeout:
                to_close.append(terminal.id)

        # Prune tracking dict of terminals that are no longer idle candidates
        for tid in list(self._first_seen_idle):
            if tid not in candidate_ids:
                self._first_seen_idle.pop(tid, None)

        for terminal_id in to_close:
            self._first_seen_idle.pop(terminal_id, None)
            LOG.info("Auto-closing idle terminal %s (idle for ≥%s min)", terminal_id, self.idle_timeout.seconds // 60)
            self.terminals.delete_terminal(terminal_id)

    def purge_orphan_logs(self) -> None:
        """Remove log files without a matching terminal record."""
        with session_scope() as db:
            existing_ids = {row[0] for row in db.query(TerminalORM.id).all()}
        log_dir = constants.TERMINAL_LOG_DIR
        if not log_dir.exists():
            return
        for path in log_dir.glob("*.log"):
            terminal_id = path.stem
            if terminal_id not in existing_ids:
                path.unlink(missing_ok=True)
