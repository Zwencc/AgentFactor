"""Pydantic schemas for LLM review and verifier runs."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class LLMProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    provider_type: str = Field(pattern="^(openai_compatible|anthropic)$")
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)
    is_active: bool = False


class LLMProviderUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    provider_type: Optional[str] = Field(default=None, pattern="^(openai_compatible|anthropic)$")
    base_url: Optional[str] = Field(default=None, min_length=1)
    api_key: Optional[str] = Field(default=None, min_length=1)
    model: Optional[str] = Field(default=None, min_length=1)
    is_active: Optional[bool] = None


class LLMProviderResponse(BaseModel):
    id: int
    name: str
    provider_type: str
    base_url: str
    model: str
    is_active: bool
    api_key_set: bool
    created_at: str
    updated_at: str


class LLMProviderTestRequest(BaseModel):
    prompt: str = "hello"


class LLMProviderTestResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


class TerminalReviewRequest(BaseModel):
    force: bool = False
    trigger_source: str = "manual"
    threshold: float = Field(default=75.0, ge=0, le=100)


class VerifierCheckResponse(BaseModel):
    id: int
    run_id: int
    check_type: str
    name: str
    status: str
    command: Optional[str]
    exit_code: Optional[int]
    score: Optional[float]
    threshold: Optional[float]
    output_excerpt: Optional[str]
    artifact_ref: Optional[str]
    created_at: str


class VerifierRunResponse(BaseModel):
    id: int
    work_item_id: str
    terminal_id: Optional[str]
    analysis_id: Optional[int]
    attempt_no: int
    trigger_source: str
    status: str
    strategy: dict[str, Any]
    summary: Optional[str]
    failure_reason: Optional[str]
    raw_artifacts: dict[str, Any]
    started_at: Optional[str]
    finished_at: Optional[str]
    created_at: str
    checks: list[VerifierCheckResponse] = Field(default_factory=list)


class TerminalReviewResponse(BaseModel):
    terminal_id: str
    analysis_id: Optional[int]
    work_item_id: Optional[str]
    status: str
    review: Optional[dict[str, Any]] = None
    review_error: Optional[str] = None
    verifier_run: Optional[VerifierRunResponse] = None

