"""SQLite database client for AgentFactor."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from agentfactor import constants
from agentfactor.models.enums import ApprovalStatus, EdgeType, InboxStatus, TerminalStatus, WorkItemStatus, WorkItemType
from agentfactor.utils.pathing import ensure_runtime_directories


class BaseModel(DeclarativeBase):
    """Declarative base class for SQLAlchemy models."""


def _build_engine(echo: bool = False):
    ensure_runtime_directories()
    return create_engine(f"sqlite:///{constants.DB_FILE}", echo=echo, future=True)


ENGINE = _build_engine()
SESSION_FACTORY = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False, future=True)


class Terminal(BaseModel):
    """Represents a tmux window managed by the orchestrator."""

    __tablename__ = "terminals"
    __table_args__ = (Index("ix_terminal_session", "session_name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_name: Mapped[str] = mapped_column(String, nullable=False)
    window_name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    agent_profile: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[TerminalStatus] = mapped_column(Enum(TerminalStatus), nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    inbox_messages: Mapped[list["InboxMessage"]] = relationship(
        back_populates="receiver", cascade="all, delete-orphan"
    )


class InboxMessage(BaseModel):
    """Message queued for delivery to a terminal."""

    __tablename__ = "inbox_messages"
    __table_args__ = (Index("ix_inbox_receiver_status", "receiver_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    receiver_id: Mapped[str] = mapped_column(String, ForeignKey("terminals.id"), nullable=False)
    sender_id: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[InboxStatus] = mapped_column(Enum(InboxStatus), nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    receiver: Mapped["Terminal"] = relationship(back_populates="inbox_messages")


class Project(BaseModel):
    """User-defined project with an optional root directory on disk."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    root_directory: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Flow(BaseModel):
    """Scheduled flow definition."""

    __tablename__ = "flows"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    schedule: Mapped[str] = mapped_column(String, nullable=False)
    agent_profile: Mapped[str] = mapped_column(String, nullable=False)
    script: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_run: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ApprovalRequest(BaseModel):
    """Queued approval for human-in-the-loop command execution."""

    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String, ForeignKey("terminals.id"), nullable=False)
    supervisor_id: Mapped[str] = mapped_column(String, nullable=False)
    command_text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_payload: Mapped[Optional[str]] = mapped_column("metadata", Text, nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False
    )
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)


class EventLog(BaseModel):
    """Immutable append-only event log — never UPDATE or DELETE rows."""

    __tablename__ = "event_log"
    __table_args__ = (
        Index("ix_event_log_terminal_time", "terminal_id", "timestamp"),
        Index("ix_event_log_type_time", "type", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    timestamp: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)


class SecurityEndpoint(BaseModel):
    """A security runtime connected to the AgentFactor control plane."""

    __tablename__ = "security_endpoints"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    socket_path: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    protocol_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityIntegration(BaseModel):
    """One ACR workspace and its currently provable protection posture."""

    __tablename__ = "security_integrations"
    __table_args__ = (
        Index("ix_security_integration_endpoint_status", "endpoint_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(String, ForeignKey("security_endpoints.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    assurance: Mapped[str] = mapped_column(String, nullable=False, default="UNKNOWN")
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    head_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    unresolved_gaps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hook_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityEvent(BaseModel):
    """A minimized projection of one upstream security event."""

    __tablename__ = "security_events"
    __table_args__ = (
        UniqueConstraint("source_id", "upstream_event_id", name="uq_security_event_source_upstream"),
        Index("ix_security_event_integration_sequence", "integration_id", "sequence"),
        Index("ix_security_event_type_occurred", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    upstream_event_id: Mapped[str] = mapped_column(String, nullable=False)
    integration_id: Mapped[str] = mapped_column(String, ForeignKey("security_integrations.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String, nullable=False)
    agent_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    native_session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    acr_session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    decision_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contract_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    previous_hash: Mapped[str] = mapped_column(String, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String, nullable=False)
    event_hash: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String, nullable=False)
    ingested_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecuritySyncCursor(BaseModel):
    """The committed upstream sequence for one security integration."""

    __tablename__ = "security_sync_cursors"

    integration_id: Mapped[str] = mapped_column(String, ForeignKey("security_integrations.id"), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityCapabilityProof(BaseModel):
    """Latest bounded proof for one integration capability."""

    __tablename__ = "security_capability_proofs"
    __table_args__ = (
        UniqueConstraint("integration_id", "capability", name="uq_security_capability_integration_name"),
        Index("ix_security_capability_integration_status", "integration_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    integration_id: Mapped[str] = mapped_column(String, ForeignKey("security_integrations.id"), nullable=False)
    receipt_id: Mapped[str] = mapped_column(String, nullable=False)
    capability: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    evidence_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metrics: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    attestation: Mapped[str] = mapped_column(String, nullable=False)
    checked_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityEventLink(BaseModel):
    """Minimized causality identifiers for one projected security event."""

    __tablename__ = "security_event_links"
    __table_args__ = (
        UniqueConstraint("source_id", "upstream_event_id", name="uq_security_event_link_source_upstream"),
        Index("ix_security_event_link_correlation", "integration_id", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    upstream_event_id: Mapped[str] = mapped_column(String, nullable=False)
    integration_id: Mapped[str] = mapped_column(String, ForeignKey("security_integrations.id"), nullable=False)
    correlation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    causation_event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    native_turn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    native_tool_use_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityActionDecision(BaseModel):
    """A minimized three-state decision for one canonical action."""

    __tablename__ = "security_action_decisions"
    __table_args__ = (
        Index("ix_security_action_decision_effect_time", "effect", "created_at"),
        Index("ix_security_action_decision_fingerprint", "action_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    action_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    effect: Mapped[str] = mapped_column(String, nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    rule_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    principal_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    surface: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    resource_digest: Mapped[str] = mapped_column(String, nullable=False)
    params_digest: Mapped[str] = mapped_column(String, nullable=False)
    enforced: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityExecutionGrant(BaseModel):
    """Short-lived one-use authorization bound to one action fingerprint."""

    __tablename__ = "security_execution_grants"
    __table_args__ = (Index("ix_security_execution_grant_fingerprint", "action_fingerprint"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    decision_id: Mapped[str] = mapped_column(String, ForeignKey("security_action_decisions.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    action_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityRiskAlert(BaseModel):
    """A non-blocking reminder or a blocked-action notification."""

    __tablename__ = "security_risk_alerts"
    __table_args__ = (Index("ix_security_risk_alert_time", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    decision_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    integration_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    action_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    effect: Mapped[str] = mapped_column(String, nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityMCPServer(BaseModel):
    """Configured MCP server identity without credentials or raw environment."""

    __tablename__ = "security_mcp_servers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    transport: Mapped[str] = mapped_column(String, nullable=False)
    config_digest: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_discovered_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityMCPToolVersion(BaseModel):
    """A content-addressed MCP tool schema observed through the gateway."""

    __tablename__ = "security_mcp_tool_versions"
    __table_args__ = (
        UniqueConstraint("server_id", "tool_name", "schema_digest", name="uq_security_mcp_tool_schema"),
        Index("ix_security_mcp_tool_current", "server_id", "tool_name", "current"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    server_id: Mapped[str] = mapped_column(String, ForeignKey("security_mcp_servers.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    schema_digest: Mapped[str] = mapped_column(String, nullable=False)
    input_schema: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityExecutionResult(BaseModel):
    """Digest-only result evidence for a gateway-mediated call."""

    __tablename__ = "security_execution_results"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    decision_id: Mapped[str] = mapped_column(String, ForeignKey("security_action_decisions.id"), nullable=False)
    grant_id: Mapped[str] = mapped_column(String, ForeignKey("security_execution_grants.id"), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_digest: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecuritySandboxSession(BaseModel):
    """Lifecycle and bounded capability claims for one public-Agent sandbox."""

    __tablename__ = "security_sandbox_sessions"
    __table_args__ = (Index("ix_security_sandbox_terminal", "terminal_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    terminal_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    backend: Mapped[str] = mapped_column(String, nullable=False)
    profile: Mapped[str] = mapped_column(String, nullable=False)
    workspace_digest: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    capabilities: Mapped[str] = mapped_column(Text, nullable=False)
    canary_status: Mapped[str] = mapped_column(String, nullable=False, default="not_run")
    canary_checked_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    canary_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    started_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityPolicy(BaseModel):
    """A named security policy with one explicitly published revision."""

    __tablename__ = "security_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    active_revision_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityPolicyRevision(BaseModel):
    """An immutable canonical policy document."""

    __tablename__ = "security_policy_revisions"
    __table_args__ = (
        UniqueConstraint("policy_id", "version", name="uq_security_policy_revision_version"),
        Index("ix_security_policy_revision_digest", "document_digest"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    policy_id: Mapped[str] = mapped_column(String, ForeignKey("security_policies.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[str] = mapped_column(Text, nullable=False)
    document_digest: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)


class SecurityPolicyBundle(BaseModel):
    """Compiled policy artifact consumed by the local security gateway."""

    __tablename__ = "security_policy_bundles"
    __table_args__ = (Index("ix_security_policy_bundle_revision", "revision_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision_id: Mapped[str] = mapped_column(String, ForeignKey("security_policy_revisions.id"), nullable=False)
    compiler_version: Mapped[str] = mapped_column(String, nullable=False)
    bundle_digest: Mapped[str] = mapped_column(String, nullable=False)
    gateway_config: Mapped[str] = mapped_column(Text, nullable=False)
    acr_delivery: Mapped[str] = mapped_column(String, nullable=False, default="NOT_CONFIGURED")
    signature: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    signing_key_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)


class SecurityPolicyRollout(BaseModel):
    """A deterministic, staged delivery plan for a signed policy bundle."""

    __tablename__ = "security_policy_rollouts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bundle_id: Mapped[str] = mapped_column(String, ForeignKey("security_policy_bundles.id"), nullable=False)
    percentage: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityPolicyRolloutTarget(BaseModel):
    """One endpoint selected by a stable rollout cohort hash."""

    __tablename__ = "security_policy_rollout_targets"
    __table_args__ = (UniqueConstraint("rollout_id", "endpoint_id", name="uq_policy_rollout_target"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    rollout_id: Mapped[str] = mapped_column(String, ForeignKey("security_policy_rollouts.id"), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(String, ForeignKey("security_endpoints.id"), nullable=False)
    cohort: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityNativeSession(BaseModel):
    """A token-bound Native SDK session and its local execution scope."""

    __tablename__ = "security_native_sessions"
    __table_args__ = (Index("ix_security_native_session_status_expiry", "status", "expires_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    principal_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    workspace_path: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_digest: Mapped[str] = mapped_column(String, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    agent_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    assurance: Mapped[str] = mapped_column(String, nullable=False, default="OBSERVED")
    taint_labels: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityDeclassification(BaseModel):
    """Evidence that a published policy rule removed session labels."""

    __tablename__ = "security_declassifications"
    __table_args__ = (Index("ix_security_declassification_session", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("security_native_sessions.id"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    purpose_digest: Mapped[str] = mapped_column(String, nullable=False)
    removed_labels: Mapped[str] = mapped_column(Text, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityCredentialLease(BaseModel):
    """One short-lived, internally consumed credential checkout without secret material."""

    __tablename__ = "security_credential_leases"
    __table_args__ = (Index("ix_security_credential_lease_session", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    credential_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("security_native_sessions.id"), nullable=False)
    surface: Mapped[str] = mapped_column(String, nullable=False)
    resource_digest: Mapped[str] = mapped_column(String, nullable=False)
    secret_digest: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityRoleBinding(BaseModel):
    """A local authorization binding for an authenticated OIDC subject."""

    __tablename__ = "security_role_bindings"
    __table_args__ = (UniqueConstraint("principal_id", "role", name="uq_security_role_binding"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    principal_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityResponseAction(BaseModel):
    """Auditable and reversible session or endpoint response action."""

    __tablename__ = "security_response_actions"
    __table_args__ = (Index("ix_security_response_target", "target_type", "target_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    reason_digest: Mapped[str] = mapped_column(String, nullable=False)
    previous_status: Mapped[str] = mapped_column(String, nullable=False)
    resulting_status: Mapped[str] = mapped_column(String, nullable=False)
    reversed_by_action_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityAuditExport(BaseModel):
    """Idempotency marker for one minimized audit record exported externally."""

    __tablename__ = "security_audit_exports"
    __table_args__ = (UniqueConstraint("destination_digest", "event_id", name="uq_security_audit_export"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    destination_digest: Mapped[str] = mapped_column(String, nullable=False)
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    batch_digest: Mapped[str] = mapped_column(String, nullable=False)
    exported_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Snapshot(BaseModel):
    """Content-addressed checkpoint of derived project state."""

    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    derived_state: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TerminalMetrics(BaseModel):
    """Rolling per-terminal health metrics sampled every N seconds."""

    __tablename__ = "terminal_metrics"
    __table_args__ = (Index("ix_metrics_terminal_time", "terminal_id", "sampled_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String, nullable=False)
    sampled_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    output_velocity_tpm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_density: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_streak_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    signal_counts: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


# Alias so stream_processor.py can import TerminalORM
TerminalORM = Terminal


class WorkItem(BaseModel):
    """Causal work graph node — a trackable unit of agent work."""

    __tablename__ = "work_items"
    __table_args__ = (
        Index("ix_work_items_project_status", "project_id", "status"),
        Index("ix_work_items_owner", "owner_terminal_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    type: Mapped[WorkItemType] = mapped_column(Enum(WorkItemType), nullable=False)
    status: Mapped[WorkItemStatus] = mapped_column(Enum(WorkItemStatus), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    owner_terminal_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    files_of_interest: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    proof_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    complexity: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)


class WorkEdge(BaseModel):
    """Directed edge in the causal work dependency graph."""

    __tablename__ = "work_edges"
    __table_args__ = (
        Index("ix_work_edges_from", "from_id"),
        Index("ix_work_edges_to", "to_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_id: Mapped[str] = mapped_column(String, nullable=False)
    to_id: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[EdgeType] = mapped_column(Enum(EdgeType), nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BlueprintJob(BaseModel):
    """Work graph blueprint generated by a planner agent."""

    __tablename__ = "blueprint_jobs"
    __table_args__ = (Index("ix_blueprint_jobs_terminal", "terminal_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    toml_content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ready")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    imported_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class WorkGraphGenerationRequest(BaseModel):
    """User input used to launch smart work graph generation."""

    __tablename__ = "work_graph_generation_requests"
    __table_args__ = (
        Index("ix_work_graph_generation_project_time", "project_id", "created_at"),
        Index("ix_work_graph_generation_terminal", "terminal_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    constraints: Mapped[str] = mapped_column(Text, nullable=False, default="")
    persona: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    mcp_capable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    base_blueprint_terminal_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProofWindow(BaseModel):
    """Bounded observation window that must collect verifiable evidence before a work item is done."""

    __tablename__ = "proof_windows"
    __table_args__ = (
        Index("ix_proof_windows_work_item", "work_item_id"),
        Index("ix_proof_windows_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_item_id: Mapped[str] = mapped_column(String, nullable=False)
    opened_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    proofs_collected: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    closed_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ContextPack(BaseModel):
    """Semantic context pack delivered to an agent terminal."""

    __tablename__ = "context_packs"
    __table_args__ = (Index("ix_context_packs_terminal_time", "terminal_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    terminal_id: Mapped[str] = mapped_column(String, nullable=False)
    sections: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_differential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    base_pack_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapabilityEstimate(BaseModel):
    """Bayesian Beta distribution estimate of provider/persona success rate per task type."""

    __tablename__ = "capability_estimates"
    __table_args__ = (
        Index("ix_capability_provider_persona_type", "provider", "persona", "task_type", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    persona: Mapped[str] = mapped_column(String, nullable=False)
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    alpha: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    beta_param: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    total_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TopologyProposal(BaseModel):
    """Topology change proposal generated by the TopologyEngine."""

    __tablename__ = "topology_proposals"
    __table_args__ = (Index("ix_topology_proposals_status_time", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    terminal_id: Mapped[str] = mapped_column(String, nullable=False)
    proposal_type: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    suggested_persona: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    metrics_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class LLMProviderConfig(BaseModel):
    """Configurable LLM provider used by semantic verifier checks."""

    __tablename__ = "llm_provider_configs"
    __table_args__ = (
        Index("ix_llm_provider_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider_type: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerifierRun(BaseModel):
    """One verification attempt for a work item."""

    __tablename__ = "verifier_runs"
    __table_args__ = (
        Index("ix_verifier_runs_work_item", "work_item_id", "attempt_no"),
        Index("ix_verifier_runs_terminal", "terminal_id"),
        Index("ix_verifier_runs_analysis", "analysis_id"),
        Index("ix_verifier_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_item_id: Mapped[str] = mapped_column(String, nullable=False)
    terminal_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    analysis_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trigger_source: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    strategy_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_artifacts: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerifierCheck(BaseModel):
    """Single check result belonging to a verifier run."""

    __tablename__ = "verifier_checks"
    __table_args__ = (
        Index("ix_verifier_checks_run", "run_id"),
        Index("ix_verifier_checks_type_status", "check_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    check_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    command: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    output_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TerminalAnalysis(BaseModel):
    """Persisted behaviour analysis for a completed terminal session."""

    __tablename__ = "terminal_analyses"
    __table_args__ = (Index("ix_terminal_analyses_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    session_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    window_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tool_stats: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    files_touched: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    commands_run: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    risk_flags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    work_item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    compliance_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conversation_turns: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    llm_review: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_review_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    review_model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    review_provider_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    review_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _migrate_columns(engine) -> None:  # type: ignore[no-untyped-def]
    """Idempotently add columns that may be missing in databases created before Phase 3."""
    stmts = [
        "ALTER TABLE work_items ADD COLUMN embedding BLOB",
        "ALTER TABLE event_log ADD COLUMN embedding BLOB",
        "ALTER TABLE terminal_analyses ADD COLUMN raw_log TEXT",
        "ALTER TABLE terminal_analyses ADD COLUMN conversation_turns TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE terminal_analyses ADD COLUMN llm_review TEXT",
        "ALTER TABLE terminal_analyses ADD COLUMN llm_review_raw TEXT",
        "ALTER TABLE terminal_analyses ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE terminal_analyses ADD COLUMN review_model TEXT",
        "ALTER TABLE terminal_analyses ADD COLUMN review_provider_id INTEGER",
        "ALTER TABLE terminal_analyses ADD COLUMN review_error TEXT",
        "ALTER TABLE terminal_analyses ADD COLUMN reviewed_at DATETIME",
        "ALTER TABLE security_sandbox_sessions ADD COLUMN canary_status VARCHAR NOT NULL DEFAULT 'not_run'",
        "ALTER TABLE security_sandbox_sessions ADD COLUMN canary_checked_at DATETIME",
        "ALTER TABLE security_sandbox_sessions ADD COLUMN canary_evidence TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE security_native_sessions ADD COLUMN taint_labels TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE security_policy_bundles ADD COLUMN signature VARCHAR",
        "ALTER TABLE security_policy_bundles ADD COLUMN signing_key_id VARCHAR",
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists — SQLite raises OperationalError


def init_db(echo: bool = False) -> None:
    """Create tables if they do not exist."""
    global ENGINE, SESSION_FACTORY
    ENGINE = _build_engine(echo=echo)
    SESSION_FACTORY = sessionmaker(
        bind=ENGINE,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    BaseModel.metadata.create_all(bind=ENGINE)
    _migrate_columns(ENGINE)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = SESSION_FACTORY()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
