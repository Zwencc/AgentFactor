"""ACR posture synchronization and minimized security-event projections."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from agentfactor.clients.database import (
    SecurityActionDecision,
    SecurityEndpoint,
    SecurityEvent,
    SecurityEventLink,
    SecurityIntegration,
    SecurityCapabilityProof,
    SecurityRiskAlert,
    SecuritySyncCursor,
    session_scope,
)
from agentfactor.security.acr_client import ACRClient, ACRClientError, ACRIncompatibleError, ACRUnavailableError
from agentfactor.security.schemas import (
    ACTION_SCHEMA_VERSION,
    CONTROL_PROTOCOL_VERSION,
    DECISION_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    REQUIRED_DECISIONS,
    REQUIRED_OPERATIONS,
    ACRCapabilities,
    ACRCanary,
    ACREvent,
    ACREventPage,
    ACRWorkspace,
)


ENDPOINT_ID = "acr-local"
DECISION_EVENTS = {
    "action.passed": "pass",
    "action.rewritten": "rewrite",
    "repair.planned": "repair",
    "action.asked": "ask",
    "action.denied": "deny",
}
CANARY_OPERATIONS = {"coverage", "canary"}
CHECK_CODES = {
    "event_chain": {"event_chain_verified", "event_chain_empty", "event_chain_invalid", "event_store_unavailable"},
    "coverage_gaps": {"coverage_gaps_clear", "coverage_gaps_open", "coverage_gap_query_failed", "coverage_unknown"},
    "contract_registry": {"registry_managed", "registry_unmanaged", "registry_invalid", "registry_unknown"},
    "policy_engine": {"policy_engine_loaded", "policy_engine_invalid", "contract_load_failed", "no_active_project_contract"},
    "workspace_baseline": {"baseline_current", "baseline_missing", "baseline_invalid", "baseline_scan_failed", "baseline_changed", "baseline_files_skipped"},
    "hook_observation": {"hook_observation_missing", "hook_history_invalid"},
    "hook.claude-code": {"hook_not_configured", "hook_not_observed", "hook_observation_stale", "hook_observed"},
    "hook.codex": {"hook_not_configured", "hook_not_observed", "hook_observation_stale", "hook_observed"},
    "guarded_execution": {"guarded_history_invalid", "guarded_launch_unobserved", "guarded_launch_without_actions", "guarded_launch_observed"},
}
CHECK_METRICS = {"open", "active", "changes", "skipped", "actions", "hook_events"}
REQUIRED_CANARY_CAPABILITIES = {"event_chain", "coverage_gaps", "contract_registry", "policy_engine", "workspace_baseline"}
SAFE_METADATA_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:@+-]{1,256}")
EVENT_HASH = re.compile(r"[0-9a-f]{64}")
ACTION_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")


class SecuritySyncError(RuntimeError):
    """Security posture could not be synchronized safely."""


class SecuritySyncService:
    def __init__(self, client: ACRClient | None = None) -> None:
        self.client = client or ACRClient()

    def capabilities(self) -> ACRCapabilities:
        capabilities = self.client.capabilities()
        self._validate_capabilities(capabilities)
        return capabilities

    def sync(self) -> dict[str, int | str]:
        try:
            capabilities = self.capabilities()
            posture = self.client.posture()
            if posture.protocol_version != CONTROL_PROTOCOL_VERSION:
                raise SecuritySyncError("ACR posture used an unsupported protocol")
            if posture.runtime_version != capabilities.runtime_version:
                raise SecuritySyncError("ACR runtime changed during synchronization")
        except (ACRClientError, SecuritySyncError) as exc:
            failure = self._record_endpoint_failure(exc)
            raise SecuritySyncError(failure) from exc

        self._upsert_endpoint(capabilities, "healthy", None)
        seen_integrations: set[str] = set()
        ingested = 0
        try:
            for workspace in posture.workspaces:
                if workspace.head_hash is not None and not EVENT_HASH.fullmatch(workspace.head_hash):
                    raise SecuritySyncError(f"ACR returned an invalid event head for workspace {workspace.workspace_id}")
                seen_integrations.add(workspace.workspace_id)
                self._upsert_integration(workspace)
                ingested += self._sync_workspace(workspace, capabilities.maximum_page_size)
            self._mark_missing_integrations(seen_integrations)
        except (ACRClientError, SecuritySyncError) as exc:
            failure = self._record_endpoint_failure(exc)
            raise SecuritySyncError(failure) from exc
        return {
            "endpoint_id": ENDPOINT_ID,
            "workspaces": len(posture.workspaces),
            "events_ingested": ingested,
        }

    def overview(self) -> dict[str, Any]:
        with session_scope() as db:
            endpoints = db.query(SecurityEndpoint).all()
            integrations = db.query(SecurityIntegration).all()
            proofs = db.query(SecurityCapabilityProof).all()
            proofs_by_integration: dict[str, list[SecurityCapabilityProof]] = {}
            for proof in proofs:
                proofs_by_integration.setdefault(proof.integration_id, []).append(proof)
            decision_counts = {
                kind: db.query(SecurityEvent).filter(SecurityEvent.decision_kind == kind).count()
                for kind in sorted(REQUIRED_DECISIONS)
            }
            event_count = db.query(SecurityEvent).count()
            action_effects = {
                effect: db.query(SecurityActionDecision).filter(SecurityActionDecision.effect == effect).count()
                for effect in ("record", "remind", "block")
            }
            reminders = db.query(SecurityRiskAlert).filter(SecurityRiskAlert.effect == "remind").count()
            blocked = db.query(SecurityRiskAlert).filter(SecurityRiskAlert.effect == "block").count()
        return {
            "endpoints": len(endpoints),
            "integrations": len(integrations),
            "degraded_integrations": sum(
                item.status != "healthy" or self._effective_assurance(item, proofs_by_integration.get(item.id, [])) == "DEGRADED"
                for item in integrations
            ),
            "events": event_count,
            "decisions": decision_counts,
            "action_effects": action_effects,
            "reminders": reminders,
            "blocked": blocked,
        }

    def endpoints(self) -> list[dict[str, Any]]:
        with session_scope() as db:
            endpoints = db.query(SecurityEndpoint).order_by(SecurityEndpoint.id).all()
            integrations = db.query(SecurityIntegration).order_by(SecurityIntegration.name).all()
            proofs = db.query(SecurityCapabilityProof).all()
            proofs_by_integration: dict[str, list[SecurityCapabilityProof]] = {}
            for proof in proofs:
                proofs_by_integration.setdefault(proof.integration_id, []).append(proof)
            by_endpoint: dict[str, list[dict[str, Any]]] = {}
            for item in integrations:
                by_endpoint.setdefault(item.endpoint_id, []).append(self._integration_dict(item, proofs_by_integration.get(item.id, [])))
            return [self._endpoint_dict(item, by_endpoint.get(item.id, [])) for item in endpoints]

    def endpoint(self, endpoint_id: str) -> dict[str, Any] | None:
        with session_scope() as db:
            endpoint = db.get(SecurityEndpoint, endpoint_id)
            if endpoint is None:
                return None
            integrations = db.query(SecurityIntegration).filter(SecurityIntegration.endpoint_id == endpoint_id).order_by(SecurityIntegration.name).all()
            proofs = db.query(SecurityCapabilityProof).filter(
                SecurityCapabilityProof.integration_id.in_([item.id for item in integrations])
            ).all() if integrations else []
            proofs_by_integration: dict[str, list[SecurityCapabilityProof]] = {}
            for proof in proofs:
                proofs_by_integration.setdefault(proof.integration_id, []).append(proof)
            values = [self._integration_dict(item, proofs_by_integration.get(item.id, [])) for item in integrations]
            return self._endpoint_dict(endpoint, values)

    def integration(self, integration_id: str) -> dict[str, Any] | None:
        with session_scope() as db:
            integration = db.get(SecurityIntegration, integration_id)
            if integration is None:
                return None
            proofs = db.query(SecurityCapabilityProof).filter(
                SecurityCapabilityProof.integration_id == integration_id
            ).order_by(SecurityCapabilityProof.capability).all()
            events = db.query(SecurityEvent).filter(
                SecurityEvent.integration_id == integration_id
            ).order_by(SecurityEvent.id.desc()).limit(50).all()
            links = self._event_links(db, events)
            value = self._integration_dict(integration, proofs)
            value["recent_events"] = [self._event_dict(item, links.get((item.source_id, item.upstream_event_id))) for item in events]
            return value

    def verify(self, integration_id: str) -> dict[str, Any]:
        with session_scope() as db:
            integration = db.get(SecurityIntegration, integration_id)
            if integration is None:
                raise SecuritySyncError(f"Unknown security integration {integration_id}")
            project_id = integration.project_id
            last_hook_at = integration.last_hook_at
        try:
            capabilities = self.capabilities()
            if not CANARY_OPERATIONS.issubset(capabilities.operations):
                raise SecuritySyncError("ACR does not expose coverage and canary operations")
            canary = self.client.canary(integration_id)
            self._validate_canary(canary, integration_id, project_id)
        except (ACRClientError, SecuritySyncError) as exc:
            failure = self._record_endpoint_failure(exc)
            raise SecuritySyncError(failure) from exc
        with session_scope() as db:
            integration = db.get(SecurityIntegration, integration_id)
            if integration is None:
                raise SecuritySyncError(f"Unknown security integration {integration_id}")
            current_capabilities = {check.capability for check in canary.checks}
            stale_proofs = db.query(SecurityCapabilityProof).filter(
                SecurityCapabilityProof.integration_id == integration_id,
                SecurityCapabilityProof.capability.not_in(current_capabilities),
            ).all()
            for proof in stale_proofs:
                db.delete(proof)
            for check in canary.checks:
                proof_id = f"{integration_id}:{check.capability}"
                proof = db.get(SecurityCapabilityProof, proof_id)
                if proof is None:
                    proof = SecurityCapabilityProof(id=proof_id, integration_id=integration_id)
                    db.add(proof)
                proof.receipt_id = canary.receipt_id
                proof.capability = check.capability
                proof.status = check.status
                proof.code = check.code
                proof.required = check.required
                proof.observed_at = check.observed_at
                proof.evidence_refs = json.dumps(check.evidence_refs, separators=(",", ":"))
                proof.metrics = json.dumps(check.metrics, separators=(",", ":"), sort_keys=True)
                proof.attestation = canary.attestation
                proof.checked_at = canary.checked_at
                proof.expires_at = canary.expires_at
            if canary.status == "pass":
                integration.assurance = "VERIFIED"
            elif canary.status == "fail":
                integration.assurance = "DEGRADED"
            else:
                integration.assurance = "OBSERVED" if last_hook_at else "UNKNOWN"
        value = self.integration(integration_id)
        if value is None:
            raise SecuritySyncError(f"Unknown security integration {integration_id}")
        return value

    def events(self, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with session_scope() as db:
            values = (
                db.query(SecurityEvent)
                .filter(SecurityEvent.id > after_id)
                .order_by(SecurityEvent.id)
                .limit(limit)
                .all()
            )
            links = self._event_links(db, values)
            return [self._event_dict(item, links.get((item.source_id, item.upstream_event_id))) for item in values]

    def _validate_capabilities(self, value: ACRCapabilities) -> None:
        if value.protocol_version != CONTROL_PROTOCOL_VERSION:
            raise SecuritySyncError("ACR control protocol is incompatible")
        if value.action_schema != ACTION_SCHEMA_VERSION or value.decision_schema != DECISION_SCHEMA_VERSION or value.event_schema != EVENT_SCHEMA_VERSION:
            raise SecuritySyncError("ACR action, decision or event schema is incompatible")
        if value.transport != "unix":
            raise SecuritySyncError("ACR control transport is incompatible")
        if not REQUIRED_OPERATIONS.issubset(value.operations):
            raise SecuritySyncError("ACR does not expose all required operations")
        if not REQUIRED_DECISIONS.issubset(value.decisions):
            raise SecuritySyncError("ACR does not expose all required decisions")
        if value.maximum_page_size < 1:
            raise SecuritySyncError("ACR reported an invalid event page size")

    def _validate_canary(self, value: ACRCanary, integration_id: str, project_id: str) -> None:
        if value.protocol_version != CONTROL_PROTOCOL_VERSION or value.workspace_id != integration_id or value.project_id != project_id:
            raise SecuritySyncError("ACR returned a mismatched canary receipt")
        if value.attestation != "local-unsigned" or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value.receipt_id):
            raise SecuritySyncError("ACR returned an invalid canary identity")
        if value.status not in {"pass", "warn", "fail"}:
            raise SecuritySyncError("ACR returned an invalid canary status")
        try:
            checked_at = datetime.fromisoformat(value.checked_at.replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(value.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SecuritySyncError("ACR returned invalid canary timestamps") from exc
        if checked_at.tzinfo is None or expires_at.tzinfo is None or expires_at <= checked_at or expires_at - checked_at > timedelta(minutes=30):
            raise SecuritySyncError("ACR returned an invalid canary validity window")
        if checked_at > datetime.now(timezone.utc) + timedelta(minutes=1):
            raise SecuritySyncError("ACR returned a canary timestamp from the future")
        seen: set[str] = set()
        required: set[str] = set()
        aggregate = "pass"
        for check in value.checks:
            if check.capability in seen or check.capability not in CHECK_CODES or check.code not in CHECK_CODES[check.capability]:
                raise SecuritySyncError("ACR returned an unknown canary capability or code")
            seen.add(check.capability)
            if check.status not in {"pass", "warn", "fail"}:
                raise SecuritySyncError("ACR returned an invalid capability status")
            if not set(check.metrics).issubset(CHECK_METRICS):
                raise SecuritySyncError("ACR returned an unknown canary metric")
            if any(not re.fullmatch(r"(?:event|object):[A-Za-z0-9_.:-]{1,200}", ref) for ref in check.evidence_refs):
                raise SecuritySyncError("ACR returned an unsafe canary evidence reference")
            if check.observed_at is not None:
                try:
                    observed_at = datetime.fromisoformat(check.observed_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise SecuritySyncError("ACR returned an invalid capability timestamp") from exc
                if observed_at.tzinfo is None:
                    raise SecuritySyncError("ACR returned an invalid capability timestamp")
            if check.required:
                required.add(check.capability)
                if check.status == "fail":
                    aggregate = "fail"
                elif check.status == "warn" and aggregate == "pass":
                    aggregate = "warn"
        if not REQUIRED_CANARY_CAPABILITIES.issubset(required):
            raise SecuritySyncError("ACR canary omitted required capabilities")
        if not any(name == "hook_observation" or name.startswith("hook.") for name in required):
            raise SecuritySyncError("ACR canary omitted hook coverage")
        if aggregate != value.status:
            raise SecuritySyncError("ACR canary status does not match its checks")

    def _record_endpoint_failure(self, error: Exception) -> str:
        if isinstance(error, ACRUnavailableError):
            status, code = "offline", "acr_unavailable"
        elif isinstance(error, (ACRIncompatibleError, SecuritySyncError)):
            status, code = "incompatible", "acr_security_evidence_rejected"
        else:
            status, code = "offline", "acr_request_failed"
        self._upsert_endpoint(None, status, code)
        with session_scope() as db:
            db.query(SecurityIntegration).filter(SecurityIntegration.endpoint_id == ENDPOINT_ID).update(
                {SecurityIntegration.status: "degraded", SecurityIntegration.assurance: "DEGRADED"}
            )
        return code

    def _upsert_endpoint(self, capabilities: ACRCapabilities | None, status: str, error: str | None) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            endpoint = db.get(SecurityEndpoint, ENDPOINT_ID)
            if endpoint is None:
                endpoint = SecurityEndpoint(
                    id=ENDPOINT_ID,
                    kind="acr",
                    display_name="Local ACR",
                    socket_path=str(self.client.socket_path),
                )
                db.add(endpoint)
            endpoint.socket_path = str(self.client.socket_path)
            endpoint.status = status
            endpoint.last_error = error
            if capabilities is not None:
                endpoint.runtime_version = capabilities.runtime_version
                endpoint.protocol_version = capabilities.protocol_version
                endpoint.last_seen_at = now

    def _upsert_integration(self, workspace: ACRWorkspace) -> None:
        with session_scope() as db:
            integration = db.get(SecurityIntegration, workspace.workspace_id)
            if integration is None:
                integration = SecurityIntegration(
                    id=workspace.workspace_id,
                    endpoint_id=ENDPOINT_ID,
                    project_id=workspace.project_id,
                    workspace_id=workspace.workspace_id,
                    name=workspace.name,
                    status=workspace.status,
                )
                db.add(integration)
            integration.endpoint_id = ENDPOINT_ID
            integration.project_id = workspace.project_id
            integration.name = workspace.name
            integration.status = workspace.status
            integration.assurance = self._assurance(workspace)
            integration.last_sequence = workspace.last_sequence
            integration.head_hash = workspace.head_hash
            integration.unresolved_gaps = workspace.unresolved_gaps
            integration.last_hook_at = workspace.last_hook_at
            integration.last_seen_at = workspace.last_seen_at

    def _sync_workspace(self, workspace: ACRWorkspace, maximum_page_size: int) -> int:
        ingested = 0
        with session_scope() as db:
            cursor = db.get(SecuritySyncCursor, workspace.workspace_id)
            after = cursor.last_sequence if cursor is not None else 0
        limit = min(200, maximum_page_size)
        while True:
            page = self.client.events(workspace.workspace_id, after, limit)
            if page.protocol_version != CONTROL_PROTOCOL_VERSION or page.workspace_id != workspace.workspace_id or page.project_id != workspace.project_id:
                raise SecuritySyncError(f"ACR returned mismatched events for workspace {workspace.workspace_id}")
            if page.next_sequence < after:
                raise SecuritySyncError(f"ACR event cursor moved backwards for workspace {workspace.workspace_id}")
            self._validate_event_chain(workspace, page, after)
            with session_scope() as db:
                for event in page.events:
                    ingested += self._ingest_event(db, workspace, event)
                cursor = db.get(SecuritySyncCursor, workspace.workspace_id)
                if cursor is None:
                    cursor = SecuritySyncCursor(integration_id=workspace.workspace_id, last_sequence=page.next_sequence)
                    db.add(cursor)
                else:
                    cursor.last_sequence = page.next_sequence
            after = page.next_sequence
            if not page.has_more:
                break
            if not page.events:
                raise SecuritySyncError(f"ACR returned an empty continuation page for workspace {workspace.workspace_id}")
        return ingested

    def _validate_event_chain(self, workspace: ACRWorkspace, page: ACREventPage, after: int) -> None:
        expected_hash = ""
        if after > 0:
            with session_scope() as db:
                previous = (
                    db.query(SecurityEvent)
                    .filter(
                        SecurityEvent.integration_id == workspace.workspace_id,
                        SecurityEvent.sequence == after,
                    )
                    .first()
                )
                if previous is None:
                    raise SecuritySyncError(f"Local security event chain is incomplete for workspace {workspace.workspace_id}")
                expected_hash = previous.event_hash
        expected_sequence = after + 1
        for event in page.events:
            if event.sequence != expected_sequence:
                raise SecuritySyncError(f"ACR event sequence is not contiguous for workspace {workspace.workspace_id}")
            if event.previous_hash != expected_hash:
                raise SecuritySyncError(f"ACR event hash chain is broken for workspace {workspace.workspace_id}")
            expected_sequence += 1
            expected_hash = event.event_hash
        expected_cursor = page.events[-1].sequence if page.events else after
        if page.next_sequence != expected_cursor:
            raise SecuritySyncError(f"ACR event cursor does not match its page for workspace {workspace.workspace_id}")

    def _ingest_event(self, db, workspace: ACRWorkspace, event: ACREvent) -> int:  # type: ignore[no-untyped-def]
        if event.schema_version != EVENT_SCHEMA_VERSION or event.workspace_id != workspace.workspace_id or event.project_id != workspace.project_id:
            raise SecuritySyncError(f"ACR returned an incompatible event for workspace {workspace.workspace_id}")
        if not self._safe_identifier(event.event_id) or not self._safe_identifier(event.type) or not self._safe_identifier(event.actor.kind):
            raise SecuritySyncError(f"ACR returned unsafe event metadata for workspace {workspace.workspace_id}")
        if not EVENT_HASH.fullmatch(event.payload_hash) or not EVENT_HASH.fullmatch(event.event_hash) or (event.previous_hash and not EVENT_HASH.fullmatch(event.previous_hash)):
            raise SecuritySyncError(f"ACR returned invalid event hashes for workspace {workspace.workspace_id}")
        source_id = f"acr:{workspace.workspace_id}"
        exists = (
            db.query(SecurityEvent)
            .filter(SecurityEvent.source_id == source_id, SecurityEvent.upstream_event_id == event.event_id)
            .first()
        )
        if exists is not None:
            self._upsert_event_link(db, workspace, event)
            return 0
        decision_kind = DECISION_EVENTS.get(event.type)
        contract_ids: list[str] = []
        if decision_kind is not None:
            payload_kind = event.payload.get("kind")
            if payload_kind != decision_kind:
                raise SecuritySyncError(f"ACR decision payload does not match event type {event.type}")
            matches = event.payload.get("matches", [])
            if not isinstance(matches, list):
                raise SecuritySyncError(f"ACR decision matches are invalid for event {event.event_id}")
            for match in matches:
                if isinstance(match, dict) and isinstance(match.get("contract_id"), str):
                    contract_id = self._safe_identifier(match["contract_id"])
                    if contract_id is not None:
                        contract_ids.append(contract_id)
            action_fingerprint = event.payload.get("action_fingerprint")
            if action_fingerprint is not None and (not isinstance(action_fingerprint, str) or not ACTION_FINGERPRINT.fullmatch(action_fingerprint)):
                raise SecuritySyncError(f"ACR returned an invalid action fingerprint for event {event.event_id}")
            if decision_kind == "ask":
                db.add(SecurityRiskAlert(
                    id=f"alert_acr_{event.event_id}",
                    integration_id=workspace.workspace_id,
                    source_event_id=event.event_id,
                    action_fingerprint=action_fingerprint or f"sha256:{event.event_hash}",
                    effect="block",
                    reason_code="acr_ask_blocked",
                ))
        db.add(SecurityEvent(
            source_id=source_id,
            upstream_event_id=event.event_id,
            integration_id=workspace.workspace_id,
            project_id=workspace.project_id,
            sequence=event.sequence,
            event_type=event.type,
            actor_kind=event.actor.kind,
            agent_kind=self._safe_identifier(event.actor.agent_kind),
            native_session_id=self._safe_identifier(event.actor.native_session_id),
            acr_session_id=self._safe_identifier(event.actor.acr_session_id),
            decision_kind=decision_kind,
            contract_ids=json.dumps(contract_ids, separators=(",", ":")),
            evidence_refs=json.dumps(self._safe_identifiers(event.evidence_refs), separators=(",", ":")),
            previous_hash=event.previous_hash,
            payload_hash=event.payload_hash,
            event_hash=event.event_hash,
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
        ))
        self._upsert_event_link(db, workspace, event)
        return 1

    @staticmethod
    def _upsert_event_link(db, workspace: ACRWorkspace, event: ACREvent) -> None:  # type: ignore[no-untyped-def]
        source_id = f"acr:{workspace.workspace_id}"
        exists = (
            db.query(SecurityEventLink)
            .filter(SecurityEventLink.source_id == source_id, SecurityEventLink.upstream_event_id == event.event_id)
            .first()
        )
        if exists is not None:
            return
        db.add(SecurityEventLink(
            source_id=source_id,
            upstream_event_id=event.event_id,
            integration_id=workspace.workspace_id,
            correlation_id=SecuritySyncService._safe_identifier(event.causality.correlation_id),
            causation_event_id=SecuritySyncService._safe_identifier(event.causality.causation_event_id),
            native_turn_id=SecuritySyncService._safe_identifier(event.causality.native_turn_id),
            native_tool_use_id=SecuritySyncService._safe_identifier(event.causality.native_tool_use_id),
        ))

    @staticmethod
    def _safe_identifier(value: str | None) -> str | None:
        if value is None or not SAFE_METADATA_IDENTIFIER.fullmatch(value):
            return None
        return value

    @classmethod
    def _safe_identifiers(cls, values: list[str]) -> list[str]:
        return [safe for value in values if (safe := cls._safe_identifier(value)) is not None]

    def _mark_missing_integrations(self, seen: set[str]) -> None:
        with session_scope() as db:
            values = db.query(SecurityIntegration).filter(SecurityIntegration.endpoint_id == ENDPOINT_ID).all()
            for value in values:
                if value.workspace_id not in seen:
                    value.status = "offline"
                    value.assurance = "UNKNOWN"

    @staticmethod
    def _assurance(workspace: ACRWorkspace) -> str:
        if workspace.status != "healthy":
            return "DEGRADED" if workspace.status in {"degraded", "conflict"} else "UNKNOWN"
        return "OBSERVED" if workspace.last_hook_at else "UNKNOWN"

    @staticmethod
    def _endpoint_dict(value: SecurityEndpoint, integrations: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": value.id,
            "kind": value.kind,
            "display_name": value.display_name,
            "runtime_version": value.runtime_version,
            "protocol_version": value.protocol_version,
            "status": value.status,
            "last_error": value.last_error,
            "last_seen_at": value.last_seen_at.isoformat() if value.last_seen_at else None,
            "integrations": integrations,
        }

    @classmethod
    def _integration_dict(cls, value: SecurityIntegration, proofs: list[SecurityCapabilityProof]) -> dict[str, Any]:
        return {
            "id": value.id,
            "project_id": value.project_id,
            "workspace_id": value.workspace_id,
            "name": value.name,
            "status": value.status,
            "assurance": cls._effective_assurance(value, proofs),
            "last_sequence": value.last_sequence,
            "head_hash": value.head_hash,
            "unresolved_gaps": value.unresolved_gaps,
            "last_hook_at": value.last_hook_at,
            "last_seen_at": value.last_seen_at,
            "proofs": [cls._proof_dict(proof) for proof in proofs],
        }

    @staticmethod
    def _effective_assurance(value: SecurityIntegration, proofs: list[SecurityCapabilityProof]) -> str:
        required = [proof for proof in proofs if proof.required]
        if not required:
            return value.assurance
        now = datetime.now(timezone.utc)
        if any(datetime.fromisoformat(proof.expires_at.replace("Z", "+00:00")) <= now for proof in required):
            return "DEGRADED"
        if any(proof.status == "fail" for proof in required):
            return "DEGRADED"
        if all(proof.status == "pass" for proof in required):
            return "VERIFIED"
        return "OBSERVED" if value.last_hook_at else "UNKNOWN"

    @staticmethod
    def _proof_dict(value: SecurityCapabilityProof) -> dict[str, Any]:
        stale = datetime.fromisoformat(value.expires_at.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        return {
            "capability": value.capability,
            "status": "stale" if stale else value.status,
            "result_status": value.status,
            "code": "receipt_expired" if stale else value.code,
            "required": value.required,
            "observed_at": value.observed_at,
            "evidence_refs": json.loads(value.evidence_refs),
            "metrics": json.loads(value.metrics),
            "attestation": value.attestation,
            "checked_at": value.checked_at,
            "expires_at": value.expires_at,
        }

    @staticmethod
    def _event_links(db, events: list[SecurityEvent]) -> dict[tuple[str, str], SecurityEventLink]:  # type: ignore[no-untyped-def]
        source_ids = {item.source_id for item in events}
        if not source_ids:
            return {}
        values = db.query(SecurityEventLink).filter(SecurityEventLink.source_id.in_(source_ids)).all()
        return {(item.source_id, item.upstream_event_id): item for item in values}

    @staticmethod
    def _event_dict(value: SecurityEvent, link: SecurityEventLink | None = None) -> dict[str, Any]:
        return {
            "id": value.id,
            "integration_id": value.integration_id,
            "project_id": value.project_id,
            "sequence": value.sequence,
            "event_type": value.event_type,
            "actor_kind": value.actor_kind,
            "agent_kind": value.agent_kind,
            "native_session_id": value.native_session_id,
            "acr_session_id": value.acr_session_id,
            "decision_kind": value.decision_kind,
            "contract_ids": json.loads(value.contract_ids),
            "evidence_refs": json.loads(value.evidence_refs),
            "event_hash": value.event_hash,
            "occurred_at": value.occurred_at,
            "recorded_at": value.recorded_at,
            "correlation_id": link.correlation_id if link else None,
            "causation_event_id": link.causation_event_id if link else None,
            "native_turn_id": link.native_turn_id if link else None,
            "native_tool_use_id": link.native_tool_use_id if link else None,
        }
