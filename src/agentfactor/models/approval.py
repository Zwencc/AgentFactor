"""Approval request models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from agentfactor.models.enums import ApprovalStatus


class ApprovalRequest(BaseModel):
    """Human-in-the-loop approval request."""

    id: int
    terminal_id: str
    supervisor_id: str
    command_text: str
    metadata_payload: Optional[str] = None
    status: ApprovalStatus
    created_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ApprovalCreateRequest(BaseModel):
    terminal_id: str
    supervisor_id: str
    command_text: str
    metadata_payload: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    reason: Optional[str] = None
