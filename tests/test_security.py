from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from agentfactor.api import main as api_main
from agentfactor.clients.database import SecurityCapabilityProof, SecurityEvent, SecurityEventLink, SecurityIntegration, SecurityRiskAlert, SecuritySyncCursor, session_scope
from agentfactor.security import api as security_api
from agentfactor.security.schemas import ACRCanary, ACRCapabilities, ACREventPage, ACRPosture
from agentfactor.security.service import SecuritySyncError, SecuritySyncService


class FakeACRClient:
    socket_path = Path("/tmp/fake-acr.sock")

    def capabilities(self) -> ACRCapabilities:
        return ACRCapabilities.model_validate({
            "protocol_version": "acr.control/v1alpha1",
            "runtime_version": "0.1.0-test",
            "action_schema": "acr.action/v1alpha1",
            "decision_schema": "acr.decision/v1alpha1",
            "event_schema": "acr.event/v1alpha1",
            "transport": "unix",
            "operations": ["capabilities", "posture", "events", "coverage", "canary"],
            "agents": ["claude-code", "codex"],
            "decisions": ["pass", "rewrite", "repair", "ask", "deny"],
            "maximum_page_size": 500,
        })

    def posture(self) -> ACRPosture:
        return ACRPosture.model_validate({
            "protocol_version": "acr.control/v1alpha1",
            "runtime_version": "0.1.0-test",
            "workspaces": [{
                "workspaceId": "ws_test",
                "projectId": "proj_test",
                "name": "test-project",
                "path": "/private/workspace",
                "canonicalPath": "/private/workspace",
                "enabled": True,
                "status": "healthy",
                "registeredAt": "2026-10-23T00:00:00Z",
                "lastSeenAt": "2026-10-23T00:01:00Z",
                "lastHookAt": "2026-10-23T00:01:00Z",
                "lastSequence": 1,
                "headHash": "c" * 64,
                "unresolvedGaps": 0,
                "detail": "project configuration is available",
            }],
        })

    def events(self, workspace_id: str, after_sequence: int, limit: int) -> ACREventPage:
        events = []
        if after_sequence == 0:
            events = [{
                "schema_version": "acr.event/v1alpha1",
                "event_id": "evt_test",
                "project_id": "proj_test",
                "workspace_id": workspace_id,
                "sequence": 1,
                "occurred_at": "2026-10-23T00:00:00Z",
                "recorded_at": "2026-10-23T00:00:01Z",
                "type": "action.denied",
                "actor": {
                    "kind": "runtime",
                    "agent_kind": "codex",
                    "native_session_id": "session-test",
                },
                "causality": {"correlation_id": "correlation-test"},
                "payload": {
                    "schema_version": "acr.decision/v1alpha1",
                    "kind": "deny",
                    "reason": "secret sk-must-not-be-stored",
                    "matches": [{"contract_id": "protect-secrets", "contract_version": 1, "reason": "sensitive"}],
                },
                "evidence_refs": ["event:evidence-test"],
                "previous_hash": "",
                "payload_hash": "b" * 64,
                "event_hash": "c" * 64,
            }]
        return ACREventPage.model_validate({
            "protocol_version": "acr.control/v1alpha1",
            "workspace_id": workspace_id,
            "project_id": "proj_test",
            "events": events,
            "next_sequence": 1,
            "has_more": False,
        })

    def canary(self, workspace_id: str) -> ACRCanary:
        checked_at = datetime.now(timezone.utc)
        checks = [
            {"capability": "event_chain", "status": "pass", "code": "event_chain_verified", "required": True},
            {"capability": "coverage_gaps", "status": "pass", "code": "coverage_gaps_clear", "required": True, "metrics": {"open": 0}},
            {"capability": "contract_registry", "status": "pass", "code": "registry_managed", "required": True, "metrics": {"active": 1}},
            {"capability": "policy_engine", "status": "pass", "code": "policy_engine_loaded", "required": True, "metrics": {"active": 1}},
            {"capability": "workspace_baseline", "status": "pass", "code": "baseline_current", "required": True, "metrics": {"changes": 0, "skipped": 0}},
            {"capability": "hook.codex", "status": "pass", "code": "hook_observed", "required": True, "observed_at": checked_at.isoformat()},
            {"capability": "guarded_execution", "status": "warn", "code": "guarded_launch_unobserved", "required": False},
        ]
        return ACRCanary.model_validate({
            "protocol_version": "acr.control/v1alpha1",
            "workspace_id": workspace_id,
            "project_id": "proj_test",
            "receipt_id": "canary_test",
            "attestation": "local-unsigned",
            "checked_at": checked_at.isoformat(),
            "expires_at": (checked_at + timedelta(minutes=15)).isoformat(),
            "status": "pass",
            "checks": checks,
        })


class BrokenChainACRClient(FakeACRClient):
    def events(self, workspace_id: str, after_sequence: int, limit: int) -> ACREventPage:
        page = super().events(workspace_id, after_sequence, limit)
        if page.events:
            page.events[0].previous_hash = "unexpected-hash"
        return page


class UnsafeCanaryACRClient(FakeACRClient):
    def canary(self, workspace_id: str) -> ACRCanary:
        value = super().canary(workspace_id)
        value.checks[0].code = "/private/workspace/secret"
        return value


class AskACRClient(FakeACRClient):
    def events(self, workspace_id: str, after_sequence: int, limit: int) -> ACREventPage:
        page = super().events(workspace_id, after_sequence, limit)
        if page.events:
            page.events[0].type = "action.asked"
            page.events[0].payload["kind"] = "ask"
            page.events[0].payload["action_fingerprint"] = "sha256:" + "d" * 64
        return page


def test_security_sync_is_idempotent_and_minimizes_payload():
    service = SecuritySyncService(FakeACRClient())

    first = service.sync()
    second = service.sync()

    assert first == {"endpoint_id": "acr-local", "workspaces": 1, "events_ingested": 1}
    assert second == {"endpoint_id": "acr-local", "workspaces": 1, "events_ingested": 0}
    with session_scope() as db:
        events = db.query(SecurityEvent).all()
        integration = db.get(SecurityIntegration, "ws_test")
        cursor = db.get(SecuritySyncCursor, "ws_test")
        assert len(events) == 1
        assert events[0].decision_kind == "deny"
        assert events[0].contract_ids == '["protect-secrets"]'
        assert "must-not-be-stored" not in " ".join(str(value) for value in vars(events[0]).values())
        assert integration is not None
        assert integration.assurance == "OBSERVED"
        assert integration.head_hash == "c" * 64
        assert cursor is not None
        assert cursor.last_sequence == 1
        links = db.query(SecurityEventLink).all()
        assert len(links) == 1
        assert links[0].correlation_id == "correlation-test"


def test_security_canary_verifies_capabilities_and_expires_safely():
    service = SecuritySyncService(FakeACRClient())
    service.sync()

    verified = service.verify("ws_test")

    assert verified["assurance"] == "VERIFIED"
    assert len(verified["proofs"]) == 7
    assert {proof["status"] for proof in verified["proofs"]} == {"pass", "warn"}
    with session_scope() as db:
        proofs = db.query(SecurityCapabilityProof).all()
        assert len(proofs) == 7
        assert all("/private/workspace" not in " ".join(str(value) for value in vars(proof).values()) for proof in proofs)
        for proof in proofs:
            proof.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    stale = service.integration("ws_test")
    assert stale is not None
    assert stale["assurance"] == "DEGRADED"
    assert {proof["status"] for proof in stale["proofs"]} == {"stale"}


def test_security_canary_rejects_non_whitelisted_metadata():
    service = SecuritySyncService(UnsafeCanaryACRClient())
    service.sync()

    with pytest.raises(SecuritySyncError, match="acr_security_evidence_rejected"):
        service.verify("ws_test")

    with session_scope() as db:
        assert db.query(SecurityCapabilityProof).count() == 0


def test_security_sync_rejects_a_broken_event_chain():
    service = SecuritySyncService(BrokenChainACRClient())

    with pytest.raises(SecuritySyncError, match="acr_security_evidence_rejected"):
        service.sync()

    with session_scope() as db:
        assert db.query(SecurityEvent).count() == 0
        assert db.get(SecuritySyncCursor, "ws_test") is None


def test_acr_ask_is_projected_as_blocking_alert():
    SecuritySyncService(AskACRClient()).sync()

    with session_scope() as db:
        alert = db.query(SecurityRiskAlert).one()
        assert alert.effect == "block"
        assert alert.reason_code == "acr_ask_blocked"
        assert alert.action_fingerprint == "sha256:" + "d" * 64


def test_security_api_exposes_overview_endpoints_and_events(api_client):
    service = SecuritySyncService(FakeACRClient())
    original = api_main.app.dependency_overrides.copy()
    api_main.app.dependency_overrides[security_api.get_security_sync_service] = lambda: service
    try:
        synced = api_client.post("/security/v1alpha1/sync")
        assert synced.status_code == 200
        assert synced.json()["events_ingested"] == 1

        overview = api_client.get("/security/v1alpha1/overview").json()
        assert overview["endpoints"] == 1
        assert overview["decisions"]["deny"] == 1

        endpoints = api_client.get("/security/v1alpha1/endpoints").json()
        assert endpoints[0]["integrations"][0]["assurance"] == "OBSERVED"
        assert "path" not in endpoints[0]["integrations"][0]

        events = api_client.get("/security/v1alpha1/events").json()
        assert events[0]["decision_kind"] == "deny"
        assert events[0]["correlation_id"] == "correlation-test"
        assert "payload" not in events[0]

        verified = api_client.post("/security/v1alpha1/integrations/ws_test/verify")
        assert verified.status_code == 200
        assert verified.json()["assurance"] == "VERIFIED"

        detail = api_client.get("/security/v1alpha1/integrations/ws_test")
        assert detail.status_code == 200
        assert detail.json()["recent_events"][0]["correlation_id"] == "correlation-test"
    finally:
        api_main.app.dependency_overrides = original
