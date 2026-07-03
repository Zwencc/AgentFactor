"""Inbox messaging between terminals."""

from __future__ import annotations

from typing import List

from agentfactor.clients.database import InboxMessage as InboxORM, session_scope
from agentfactor.models.enums import InboxStatus, TerminalStatus
from agentfactor.models.inbox import InboxMessage
from agentfactor.services.terminal_service import TerminalService

IDLE_STATUSES = {TerminalStatus.READY, TerminalStatus.COMPLETED}


class InboxService:
    """Queues and delivers messages between terminals."""

    def __init__(self, terminal_service: TerminalService) -> None:
        self.terminals = terminal_service

    def queue_message(
        self,
        sender_id: str,
        receiver_id: str,
        message: str,
        *,
        dedupe: bool = False,
    ) -> InboxMessage | None:
        """Persist a message with PENDING status."""
        with session_scope() as db:
            if dedupe:
                existing = (
                    db.query(InboxORM)
                    .filter(
                        InboxORM.receiver_id == receiver_id,
                        InboxORM.sender_id == sender_id,
                        InboxORM.message == message,
                        InboxORM.status.in_([InboxStatus.PENDING, InboxStatus.IN_FLIGHT]),
                    )
                    .order_by(InboxORM.created_at.desc())
                    .first()
                )
                if existing:
                    return InboxMessage.model_validate(existing, from_attributes=True)

            inbox = InboxORM(
                receiver_id=receiver_id,
                sender_id=sender_id,
                message=message,
                status=InboxStatus.PENDING,
            )
            db.add(inbox)
            db.flush()
            db.refresh(inbox)
        return InboxMessage.model_validate(inbox, from_attributes=True)

    def list_messages(self, receiver_id: str) -> List[InboxMessage]:
        with session_scope() as db:
            messages = (
                db.query(InboxORM)
                .filter(InboxORM.receiver_id == receiver_id)
                .order_by(InboxORM.created_at.asc())
                .all()
            )
            return [InboxMessage.model_validate(obj, from_attributes=True) for obj in messages]

    def deliver_pending(self, receiver_id: str) -> None:
        """Attempt to deliver pending messages by injecting them into the receiver terminal."""
        try:
            provider = self.terminals.ensure_provider_loaded(receiver_id)
        except Exception:
            self._mark_pending_failed(receiver_id)
            return
        if provider.get_status() not in IDLE_STATUSES:
            return

        # Fetch and mark IN_FLIGHT in one transaction, then close it before calling
        # send_input — SQLite allows only one writer at a time, and send_input opens
        # its own session internally.
        with session_scope() as db:
            pending = (
                db.query(InboxORM)
                .filter(InboxORM.receiver_id == receiver_id, InboxORM.status == InboxStatus.PENDING)
                .order_by(InboxORM.created_at.asc())
                .all()
            )
            entries = [(entry.id, entry.sender_id, entry.message) for entry in pending]
            for entry in pending:
                entry.status = InboxStatus.IN_FLIGHT

        for entry_id, sender_id, message in entries:
            formatted = f"[INBOX:{sender_id}] {message}"
            new_status = InboxStatus.DELIVERED
            try:
                self.terminals.send_input(receiver_id, formatted)
            except Exception:
                new_status = InboxStatus.FAILED
            with session_scope() as db:
                entry = db.get(InboxORM, entry_id)
                if entry:
                    entry.status = new_status

    def mark_failed(self, message_id: int) -> None:
        with session_scope() as db:
            message = db.get(InboxORM, message_id)
            if message:
                message.status = InboxStatus.FAILED

    def _mark_pending_failed(self, receiver_id: str) -> None:
        with session_scope() as db:
            pending = (
                db.query(InboxORM)
                .filter(InboxORM.receiver_id == receiver_id, InboxORM.status == InboxStatus.PENDING)
                .all()
            )
            for entry in pending:
                entry.status = InboxStatus.FAILED

    def deliver_all_pending(self) -> None:
        """Deliver pending messages for every receiver terminal."""
        with session_scope() as db:
            receivers = [
                row[0]
                for row in db.query(InboxORM.receiver_id)
                .filter(InboxORM.status == InboxStatus.PENDING)
                .distinct()
                .all()
            ]
        for receiver_id in receivers:
            self.deliver_pending(receiver_id)
