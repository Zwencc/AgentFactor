"""Human-in-the-loop approval workflow service."""

from __future__ import annotations

from datetime import datetime, timezone
from json import dumps
from pathlib import Path
from typing import List, Optional

from agentfactor import constants
from agentfactor.clients.database import ApprovalRequest as ApprovalORM, session_scope
from agentfactor.models.approval import ApprovalRequest
from agentfactor.models.enums import ApprovalStatus
from agentfactor.services.inbox_service import InboxService
from agentfactor.services.terminal_service import TerminalService


class ApprovalService:
    """Persists approvals and executes approved commands."""

    def __init__(
        self,
        terminal_service: TerminalService,
        inbox_service: InboxService,
    ) -> None:
        self.terminals = terminal_service
        self.inbox = inbox_service

    def request_approval(
        self,
        terminal_id: str,
        supervisor_id: str,
        command_text: str,
        metadata_payload: Optional[str] = None,
    ) -> ApprovalRequest:
        approval = ApprovalORM(
            terminal_id=terminal_id,
            supervisor_id=supervisor_id,
            command_text=command_text,
            metadata_payload=metadata_payload,
            status=ApprovalStatus.PENDING,
        )
        with session_scope() as db:
            db.add(approval)
            db.flush()
            db.refresh(approval)
        message = f"Approval required for {terminal_id}: {command_text}"
        self.inbox.queue_message(sender_id=terminal_id, receiver_id=supervisor_id, message=message)
        approval_model = ApprovalRequest.model_validate(approval, from_attributes=True)
        self._append_audit("REQUESTED", approval_model)
        return approval_model

    def list_requests(self, status: Optional[ApprovalStatus] = None) -> List[ApprovalRequest]:
        with session_scope() as db:
            query = db.query(ApprovalORM).order_by(ApprovalORM.created_at.asc())
            if status:
                query = query.filter(ApprovalORM.status == status)
            approvals = query.all()
        return [ApprovalRequest.model_validate(obj, from_attributes=True) for obj in approvals]

    def approve(self, request_id: int) -> ApprovalRequest:
        with session_scope() as db:
            approval = db.get(ApprovalORM, request_id)
            if not approval:
                raise RuntimeError(f"Approval request {request_id} not found.")
            approval.status = ApprovalStatus.APPROVED
            approval.decided_at = datetime.now(timezone.utc)
            command = approval.command_text
            terminal_id = approval.terminal_id
        self.terminals.send_input(terminal_id, command)
        approval_model = ApprovalRequest.model_validate(approval, from_attributes=True)
        self._append_audit("APPROVED", approval_model)
        return approval_model

    def deny(self, request_id: int, reason: Optional[str] = None) -> ApprovalRequest:
        with session_scope() as db:
            approval = db.get(ApprovalORM, request_id)
            if not approval:
                raise RuntimeError(f"Approval request {request_id} not found.")
            approval.status = ApprovalStatus.DENIED
            approval.decided_at = datetime.now(timezone.utc)
            if reason:
                self.inbox.queue_message(
                    sender_id=approval.supervisor_id,
                    receiver_id=approval.terminal_id,
                    message=f"Approval denied: {reason}",
                )
        approval_model = ApprovalRequest.model_validate(approval, from_attributes=True)
        self._append_audit("DENIED", approval_model, reason=reason)
        return approval_model

    def get(self, request_id: int) -> Optional[ApprovalRequest]:
        with session_scope() as db:
            approval = db.get(ApprovalORM, request_id)
            return ApprovalRequest.model_validate(approval, from_attributes=True) if approval else None

    def _append_audit(self, action: str, approval: ApprovalRequest, reason: Optional[str] = None) -> None:
        """Append audit trail entries to a log file."""
        approvals_dir = constants.APPROVALS_DIR
        approvals_dir.mkdir(parents=True, exist_ok=True)
        log_file = approvals_dir / "audit.log"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "request_id": approval.id,
            "terminal_id": approval.terminal_id,
            "supervisor_id": approval.supervisor_id,
            "status": approval.status.value,
            "reason": reason,
        }
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(dumps(entry) + "\n")
