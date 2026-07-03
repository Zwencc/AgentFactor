"""Pydantic schemas for the causal work graph (Phase 2)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from agentfactor.models.enums import EdgeType, ProofType, WorkItemStatus, WorkItemType


class WorkItemCreateRequest(BaseModel):
    project_id: str
    title: str
    description: str = ""
    type: WorkItemType = WorkItemType.FEATURE
    priority: int = Field(default=3, ge=1, le=5)
    owner_terminal_id: Optional[str] = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_of_interest: list[str] = Field(default_factory=list)
    proof_requirements: Optional[list[ProofType]] = None
    complexity: int = Field(default=3, ge=1, le=5)


class WorkItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[WorkItemStatus] = None
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    owner_terminal_id: Optional[str] = None
    acceptance_criteria: Optional[list[str]] = None
    files_of_interest: Optional[list[str]] = None
    proof_requirements: Optional[list[ProofType]] = None
    complexity: Optional[int] = Field(default=None, ge=1, le=5)


class WorkItemResponse(BaseModel):
    id: str
    project_id: str
    title: str
    description: str
    type: WorkItemType
    status: WorkItemStatus
    priority: int
    owner_terminal_id: Optional[str]
    acceptance_criteria: list[str]
    files_of_interest: list[str]
    proof_requirements: Optional[list[ProofType]]
    complexity: int
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class WorkEdgeCreateRequest(BaseModel):
    from_id: str
    to_id: str
    type: EdgeType
    created_by: str
    note: Optional[str] = None


class WorkEdgeResponse(BaseModel):
    id: int
    from_id: str
    to_id: str
    type: EdgeType
    created_by: str
    note: Optional[str]
    created_at: str

    model_config = {"from_attributes": True}


class WorkGraphResponse(BaseModel):
    project_id: str
    work_items: list[WorkItemResponse]
    edges: list[WorkEdgeResponse]
    critical_path: list[str]
    scope_conflicts: list[dict[str, Any]]


class ProofWindowResponse(BaseModel):
    id: int
    work_item_id: str
    opened_at: str
    expires_at: str
    status: str
    proofs_collected: list[dict[str, Any]]
    closed_at: Optional[str]
