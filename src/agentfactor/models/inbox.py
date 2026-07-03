"""Inbox message models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from agentfactor.models.enums import InboxStatus


class InboxMessage(BaseModel):
    """Message queued for delivery between terminals."""

    id: int
    receiver_id: str
    sender_id: str
    message: str
    status: InboxStatus
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InboxCreateRequest(BaseModel):
    sender_id: str
    receiver_id: str
    message: str
