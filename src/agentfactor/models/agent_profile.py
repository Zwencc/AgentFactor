"""Agent profile representation."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    type: str
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)


class AgentProfile(BaseModel):
    """In-memory structure describing an agent profile."""

    name: str
    description: str
    default_provider: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    tools: List[str] = Field(default_factory=list)
    allowedTools: Optional[List[str]] = None
    toolAliases: Dict[str, str] = Field(default_factory=dict)
    toolsSettings: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    mcpServers: Dict[str, MCPServerConfig] = Field(default_factory=dict)
    variables: Dict[str, str] = Field(default_factory=dict)
    prompt: Optional[str] = None
    notes: Optional[str] = None
    body: str = ""
