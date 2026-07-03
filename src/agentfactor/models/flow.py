"""Flow models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class Flow(BaseModel):
    """Scheduled flow definition."""

    name: str
    file_path: str
    schedule: str
    agent_profile: str
    script: Optional[str] = None
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    enabled: bool = True

    class Config:
        from_attributes = True


class FlowCreateRequest(BaseModel):
    name: str
    file_path: str
    schedule: str
    agent_profile: str
    script: Optional[str] = None
