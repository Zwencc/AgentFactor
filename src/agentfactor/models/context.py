"""Pydantic schemas for context pack API endpoints (Phase 3)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextPackRequest(BaseModel):
    query: str = Field(description="Semantic query used to retrieve relevant items.")
    token_budget: int = Field(default=8000, ge=1000, le=32000)


class DifferentialPackRequest(BaseModel):
    base_pack_id: str = Field(description="ID of the pack to diff against.")
    query: str
