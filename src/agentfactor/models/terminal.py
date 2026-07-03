"""Pydantic terminal models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from agentfactor.models.enums import TerminalStatus


class Terminal(BaseModel):
    """Terminal description exposed over the API."""

    id: str
    session_name: str
    window_name: str
    provider: str
    agent_profile: Optional[str] = None
    status: TerminalStatus
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TerminalCreateRequest(BaseModel):
    provider: str
    role: str = "worker"
    agent_profile: Optional[str] = None
    working_directory: Optional[str] = None


class TerminalInputRequest(BaseModel):
    message: str
    requires_approval: bool = False
    supervisor_id: Optional[str] = None
    metadata_payload: Optional[str] = None
