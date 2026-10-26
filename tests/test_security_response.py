import json

import pytest

from agentfactor.api import main as api_main
from agentfactor.clients.database import (
    SecurityEndpoint,
    SecurityResponseAction,
    session_scope,
)
from agentfactor.security import api as security_api
from agentfactor.security.access import SecurityAccessError, SecurityAccessService
from agentfactor.security.executor import NativeExecutorError, NativeExecutorService
from agentfactor.security.gateway import SecurityGatewayService
from agentfactor.security.schemas import NativeFilesystemRequest, NativeSessionCreateRequest


def _native(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "gateway.json"
    config.write_text(json.dumps({
        "policy_version": "response-v1",
        "default_effect": "block",
        "rules": [{
            "id": "list",
            "effect": "record",
            "principals": ["agent"],
            "surfaces": ["filesystem"],
            "operations": ["list"],
            "resource_prefixes": [str(workspace)],
        }],
        "mcp_servers": {},
    }))
    service = NativeExecutorService(SecurityGatewayService(config))
    native_session = service.create_session(NativeSessionCreateRequest.model_validate({
        "principal": {"id": "agent"},
        "project_id": "project",
        "workspace_path": str(workspace),
        "agent": {"name": "response-test"},
    }))
    return service, native_session


def test_authenticated_responder_can_reversibly_block_session(api_client, tmp_path, monkeypatch):
    service, native_session = _native(tmp_path)
    monkeypatch.setenv("AGENTFACTOR_SECURITY_ADMIN_TOKEN", "admin-secret")
    original = api_main.app.dependency_overrides.copy()
    api_main.app.dependency_overrides[security_api.get_native_executor_service] = lambda: service
    headers = {"authorization": "Bearer admin-secret"}
    try:
        unauthenticated = api_client.post(
            f"/security/v1alpha1/response/sessions/{native_session['id']}/block",
            json={"reason": "incident"},
        )
        assert unauthenticated.status_code == 401
        blocked = api_client.post(
            f"/security/v1alpha1/response/sessions/{native_session['id']}/block",
            json={"reason": "incident"},
            headers=headers,
        )
        assert blocked.json()["resulting_status"] == "blocked"
        with pytest.raises(NativeExecutorError, match="native_session_unauthorized"):
            service.filesystem(NativeFilesystemRequest(
                session_id=native_session["id"],
                token=native_session["token"],
                operation="list",
                path=".",
            ))
        released = api_client.post(
            f"/security/v1alpha1/response/sessions/{native_session['id']}/unblock",
            json={"reason": "investigation complete"},
            headers=headers,
        )
        assert released.json()["resulting_status"] == "active"
        assert service.filesystem(NativeFilesystemRequest(
            session_id=native_session["id"],
            token=native_session["token"],
            operation="list",
            path=".",
        ))["assurance"] == "EXECUTOR_ENFORCED"
        with session_scope() as db:
            assert db.query(SecurityResponseAction).count() == 2
    finally:
        api_main.app.dependency_overrides = original


def test_endpoint_quarantine_and_rbac_binding(api_client, monkeypatch):
    monkeypatch.setenv("AGENTFACTOR_SECURITY_ADMIN_TOKEN", "admin-secret")
    with session_scope() as db:
        db.add(SecurityEndpoint(
            id="endpoint-1",
            kind="acr",
            display_name="Endpoint",
            socket_path="/tmp/acr.sock",
            status="healthy",
        ))
    headers = {"authorization": "Bearer admin-secret"}
    bound = api_client.post(
        "/security/v1alpha1/admin/role-bindings",
        json={"principal_id": "incident-responder", "role": "responder"},
        headers=headers,
    )
    assert bound.status_code == 200
    SecurityAccessService().authorize("incident-responder", "response:execute")
    with pytest.raises(SecurityAccessError, match="permission_denied"):
        SecurityAccessService().authorize("incident-responder", "policy:publish")

    quarantined = api_client.post(
        "/security/v1alpha1/response/endpoints/endpoint-1/quarantine",
        json={"reason": "compromise suspected"},
        headers=headers,
    )
    assert quarantined.json()["resulting_status"] == "quarantined"
    released = api_client.post(
        "/security/v1alpha1/response/endpoints/endpoint-1/release",
        json={"reason": "reimaged"},
        headers=headers,
    )
    assert released.json()["resulting_status"] == "unknown"
