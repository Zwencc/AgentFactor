"""Pydantic schemas for topology and capability API endpoints (Phase 4)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class TopologyProposalResponse(BaseModel):
    id: str
    terminal_id: str
    proposal_type: str
    reason: str
    suggested_provider: Optional[str]
    suggested_persona: Optional[str]
    metrics_snapshot: Any
    status: str
    created_at: str
    decided_at: Optional[str]


class CapabilityEstimateResponse(BaseModel):
    provider: str
    persona: str
    task_type: str
    alpha: float
    beta_param: float
    total_attempts: int
    mean: float
    last_updated: Optional[str]
