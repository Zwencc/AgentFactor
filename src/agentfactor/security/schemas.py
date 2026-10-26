"""Validated messages exchanged with the ACR control protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CONTROL_PROTOCOL_VERSION = "acr.control/v1alpha1"
ACTION_SCHEMA_VERSION = "acr.action/v1alpha1"
DECISION_SCHEMA_VERSION = "acr.decision/v1alpha1"
EVENT_SCHEMA_VERSION = "acr.event/v1alpha1"
REQUIRED_DECISIONS = {"pass", "rewrite", "repair", "ask", "deny"}
REQUIRED_OPERATIONS = {"capabilities", "posture", "events"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ACRCapabilities(StrictModel):
    protocol_version: str
    runtime_version: str
    action_schema: str
    decision_schema: str
    event_schema: str
    transport: str
    operations: list[str]
    agents: list[str]
    decisions: list[str]
    maximum_page_size: int


class ACRWorkspace(StrictModel):
    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    name: str
    path: str
    canonical_path: str = Field(alias="canonicalPath")
    enabled: bool
    status: str
    registered_at: str = Field(alias="registeredAt")
    last_seen_at: str | None = Field(default=None, alias="lastSeenAt")
    last_hook_at: str | None = Field(default=None, alias="lastHookAt")
    last_sequence: int = Field(alias="lastSequence")
    head_hash: str | None = Field(default=None, alias="headHash")
    unresolved_gaps: int = Field(alias="unresolvedGaps")
    detail: str


class ACRPosture(StrictModel):
    protocol_version: str
    runtime_version: str
    workspaces: list[ACRWorkspace]


class ACRActor(StrictModel):
    kind: str
    agent_kind: str | None = None
    native_session_id: str | None = None
    acr_session_id: str | None = None


class ACRCausality(StrictModel):
    correlation_id: str | None = None
    causation_event_id: str | None = None
    native_turn_id: str | None = None
    native_tool_use_id: str | None = None


class ACREvent(StrictModel):
    schema_version: str
    event_id: str
    project_id: str
    workspace_id: str
    sequence: int
    occurred_at: str
    recorded_at: str
    type: str
    actor: ACRActor
    causality: ACRCausality
    payload: dict[str, Any]
    evidence_refs: list[str]
    previous_hash: str
    payload_hash: str
    event_hash: str


class ACREventPage(StrictModel):
    protocol_version: str
    workspace_id: str
    project_id: str
    events: list[ACREvent]
    next_sequence: int
    has_more: bool


class ACRCapabilityCheck(StrictModel):
    capability: str
    status: str
    code: str
    required: bool
    observed_at: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)


class ACRCoverage(StrictModel):
    protocol_version: str
    workspace_id: str
    project_id: str
    checked_at: str
    status: str
    checks: list[ACRCapabilityCheck]


class ACRCanary(StrictModel):
    protocol_version: str
    workspace_id: str
    project_id: str
    receipt_id: str
    attestation: str
    checked_at: str
    expires_at: str
    status: str
    checks: list[ACRCapabilityCheck]


class SecurityPrincipal(StrictModel):
    id: str
    authn: str = "local"


class SecurityAgent(StrictModel):
    name: str
    version: str | None = None


class SecurityActionContext(StrictModel):
    project_id: str
    session_id: str
    cwd: str
    tool_call_id: str | None = None


class SecurityActionTarget(StrictModel):
    surface: Literal["shell", "filesystem", "mcp", "network", "model", "credential"]
    operation: str
    resource: str
    params: dict[str, Any] = Field(default_factory=dict)


class CanonicalSecurityAction(StrictModel):
    schema_version: Literal["agentfactor.action/v1alpha1"] = "agentfactor.action/v1alpha1"
    principal: SecurityPrincipal
    endpoint_id: str
    agent: SecurityAgent
    integration_mode: Literal["adapter", "native", "gateway"]
    context: SecurityActionContext
    action: SecurityActionTarget


class ConsumeGrantRequest(StrictModel):
    token: str
    action: CanonicalSecurityAction


class MCPCallRequest(StrictModel):
    principal: SecurityPrincipal
    endpoint_id: str
    agent: SecurityAgent
    integration_mode: Literal["adapter", "native", "gateway"] = "gateway"
    project_id: str
    session_id: str
    cwd: str
    tool_call_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPInventoryRequest(StrictModel):
    principal: SecurityPrincipal
    endpoint_id: str
    agent: SecurityAgent
    integration_mode: Literal["adapter", "native", "gateway"] = "gateway"
    project_id: str
    session_id: str
    cwd: str
    tool_call_id: str | None = None


class SecurityPolicyCreateRequest(StrictModel):
    id: str
    display_name: str


DataLabel = Literal["public", "internal", "source-code", "pii", "credential", "untrusted-content"]


class DataResourceRule(StrictModel):
    id: str
    resource_prefixes: list[str]
    labels: list[DataLabel]


class DataEgressRule(StrictModel):
    id: str
    surfaces: list[Literal["model", "network", "mcp"]]
    destination_prefixes: list[str]
    allowed_labels: list[DataLabel]


class DeclassificationRule(StrictModel):
    id: str
    purpose: str
    remove_labels: list[DataLabel]


class NetworkRouteRule(StrictModel):
    id: str
    schemes: list[Literal["http", "https"]] = Field(default_factory=lambda: ["https"])
    host_patterns: list[str]
    ports: list[int] = Field(default_factory=lambda: [443])
    allow_private_ips: bool = False


class CredentialRule(StrictModel):
    id: str
    source_env: str
    allowed_surfaces: list[Literal["model", "network", "mcp"]]
    resource_prefixes: list[str]
    ttl_seconds: int = Field(default=60, ge=1, le=300)


class ModelProviderRule(StrictModel):
    id: str
    base_url: str
    models: list[str]
    credential_id: str


class SecurityPolicyRevisionCreateRequest(StrictModel):
    default_effect: Literal["record", "remind", "block"] = "block"
    rules: list[dict[str, Any]] = Field(default_factory=list)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    data_rules: list[DataResourceRule] = Field(default_factory=list)
    egress_rules: list[DataEgressRule] = Field(default_factory=list)
    declassification_rules: list[DeclassificationRule] = Field(default_factory=list)
    network_routes: list[NetworkRouteRule] = Field(default_factory=list)
    credentials: list[CredentialRule] = Field(default_factory=list)
    model_providers: list[ModelProviderRule] = Field(default_factory=list)


class NativeSessionCreateRequest(StrictModel):
    principal: SecurityPrincipal
    project_id: str
    workspace_path: str
    agent: SecurityAgent
    ttl_seconds: int = Field(default=28800, ge=60, le=86400)


class NativeSessionCloseRequest(StrictModel):
    token: str


class NativeFilesystemRequest(StrictModel):
    session_id: str
    token: str
    operation: Literal["read_text", "write_text", "mkdir", "delete_file", "list"]
    path: str
    content: str | None = None


class NativeProcessRequest(StrictModel):
    session_id: str
    token: str
    argv: list[str] = Field(min_length=1, max_length=256)
    cwd: str = "."
    timeout_seconds: int = Field(default=120, ge=1, le=300)


class NativeMCPRequest(StrictModel):
    session_id: str
    token: str
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class NativeNetworkRequest(StrictModel):
    session_id: str
    token: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class NativeModelRequest(StrictModel):
    session_id: str
    token: str
    provider_id: str
    model: str
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=256)
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=120, ge=1, le=300)


class NativeDeclassificationRequest(StrictModel):
    session_id: str
    token: str
    rule_id: str
    purpose: str


class RoleBindingCreateRequest(StrictModel):
    principal_id: str
    role: Literal["viewer", "policy_author", "policy_publisher", "responder", "admin"]


class SecurityResponseRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=1024)


class PolicyRolloutCreateRequest(StrictModel):
    percentage: int = Field(ge=1, le=100)
