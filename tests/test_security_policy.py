import json
import hashlib
import hmac

import pytest

from agentfactor.api import main as api_main
from agentfactor.clients.database import SecurityEndpoint, session_scope
from agentfactor.security import api as security_api
from agentfactor.security.gateway import SecurityGatewayService
from agentfactor.security.policy import SecurityPolicyError, SecurityPolicyService
from agentfactor.security.schemas import (
    CanonicalSecurityAction,
    SecurityPolicyCreateRequest,
    SecurityPolicyRevisionCreateRequest,
)


def _action() -> CanonicalSecurityAction:
    return CanonicalSecurityAction.model_validate({
        "principal": {"id": "developer"},
        "endpoint_id": "local",
        "agent": {"name": "codex"},
        "integration_mode": "native",
        "context": {
            "project_id": "project",
            "session_id": "session",
            "cwd": "/workspace/project",
        },
        "action": {
            "surface": "shell",
            "operation": "execute",
            "resource": "command://pytest",
        },
    })


def test_policy_revision_is_immutable_and_publishable(tmp_path):
    config_path = tmp_path / "security-gateway.json"
    service = SecurityPolicyService(config_path)
    service.create_policy(SecurityPolicyCreateRequest(id="engineering", display_name="Engineering"))
    request = SecurityPolicyRevisionCreateRequest(
        default_effect="block",
        rules=[{
            "id": "record-tests",
            "effect": "record",
            "surfaces": ["shell"],
            "operations": ["execute"],
            "resource_prefixes": ["command://pytest"],
        }],
    )

    revision = service.create_revision("engineering", request)
    request.rules[0]["effect"] = "block"
    bundle = service.publish("engineering", revision["id"])

    assert bundle["status"] == "published"
    assert bundle["acr_delivery"] == "NOT_CONFIGURED"
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(config_path.read_text())["rules"][0]["effect"] == "record"
    decision = SecurityGatewayService(config_path).evaluate(_action())
    assert decision["effect"] == "record"
    assert decision["policy_version"] == "engineering.v1"
    assert service.publish("engineering", revision["id"])["id"] == bundle["id"]


def test_policy_revision_rejects_invalid_gateway_configuration(tmp_path):
    service = SecurityPolicyService(tmp_path / "security-gateway.json")
    service.create_policy(SecurityPolicyCreateRequest(id="engineering", display_name="Engineering"))

    with pytest.raises(SecurityPolicyError, match="gateway_config_invalid"):
        service.create_revision("engineering", SecurityPolicyRevisionCreateRequest(
            rules=[{"id": "invalid", "effect": "allow"}],
        ))


def test_published_bundle_is_signed_when_trusted_key_is_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTFACTOR_POLICY_SIGNING_KEY", "test-signing-key")
    monkeypatch.setenv("AGENTFACTOR_POLICY_SIGNING_KEY_ID", "test-key-v1")
    service = SecurityPolicyService(tmp_path / "security-gateway.json")
    service.create_policy(SecurityPolicyCreateRequest(id="signed", display_name="Signed"))
    revision = service.create_revision("signed", SecurityPolicyRevisionCreateRequest())

    bundle = service.publish("signed", revision["id"])

    expected = hmac.new(
        b"test-signing-key", bundle["bundle_digest"].encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert bundle["signature"] == f"hmac-sha256:{expected}"
    assert bundle["signing_key_id"] == "test-key-v1"


def test_signed_bundle_rollout_is_stable_and_gateway_drift_is_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTFACTOR_POLICY_SIGNING_KEY", "test-signing-key")
    service = SecurityPolicyService(tmp_path / "security-gateway.json")
    service.create_policy(SecurityPolicyCreateRequest(id="rollout", display_name="Rollout"))
    revision = service.create_revision("rollout", SecurityPolicyRevisionCreateRequest())
    service.publish("rollout", revision["id"])
    with session_scope() as db:
        for endpoint_id in ("endpoint-a", "endpoint-b", "endpoint-c"):
            db.add(SecurityEndpoint(
                id=endpoint_id,
                kind="acr",
                display_name=endpoint_id,
                socket_path=f"/tmp/{endpoint_id}.sock",
                status="healthy",
            ))

    first = service.create_rollout("rollout", revision["id"], 50, "publisher")
    second = service.create_rollout("rollout", revision["id"], 50, "publisher")
    assert [(item["endpoint_id"], item["cohort"]) for item in first["targets"]] == [
        (item["endpoint_id"], item["cohort"]) for item in second["targets"]
    ]
    assert service.verify_active_gateway("rollout")["status"] == "healthy"
    service.gateway.config_path.write_text(json.dumps({
        "policy_version": "tampered",
        "default_effect": "block",
        "rules": [],
        "mcp_servers": {},
    }))
    assert service.verify_active_gateway("rollout")["status"] == "drifted"


def test_policy_api_creates_revisions_and_publishes(api_client, tmp_path):
    service = SecurityPolicyService(tmp_path / "security-gateway.json")
    original = api_main.app.dependency_overrides.copy()
    api_main.app.dependency_overrides[security_api.get_security_policy_service] = lambda: service
    try:
        created = api_client.post("/security/v1alpha1/policies", json={
            "id": "engineering",
            "display_name": "Engineering",
        })
        assert created.status_code == 200
        revision = api_client.post("/security/v1alpha1/policies/engineering/revisions", json={
            "default_effect": "remind",
            "rules": [],
            "mcp_servers": {},
        })
        assert revision.status_code == 200
        revision_id = revision.json()["id"]

        published = api_client.post(
            f"/security/v1alpha1/policies/engineering/revisions/{revision_id}/publish"
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"
        assert api_client.get("/security/v1alpha1/policies").json()[0]["status"] == "active"
        assert api_client.get("/security/v1alpha1/policies/engineering/revisions").json()[0]["status"] == "published"
    finally:
        api_main.app.dependency_overrides = original
