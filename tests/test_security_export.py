import json

import httpx

from agentfactor.security.executor import NativeExecutorService
from agentfactor.security.export import SecurityAuditExporter
from agentfactor.security.gateway import SecurityGatewayService
from agentfactor.security.schemas import NativeFilesystemRequest, NativeSessionCreateRequest


def test_audit_export_is_minimized_and_idempotent(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "secret.txt").write_text("must-not-be-exported")
    config = tmp_path / "gateway.json"
    config.write_text(json.dumps({
        "policy_version": "export-v1",
        "default_effect": "block",
        "rules": [{
            "id": "read",
            "effect": "record",
            "principals": ["agent"],
            "surfaces": ["filesystem"],
            "operations": ["read_text"],
            "resource_prefixes": [str(workspace)],
        }],
        "mcp_servers": {},
    }))
    executor = NativeExecutorService(SecurityGatewayService(config))
    native_session = executor.create_session(NativeSessionCreateRequest.model_validate({
        "principal": {"id": "agent"},
        "project_id": "project",
        "workspace_path": str(workspace),
        "agent": {"name": "export-test"},
    }))
    executor.filesystem(NativeFilesystemRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        operation="read_text",
        path="secret.txt",
    ))
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setenv("AGENTFACTOR_SIEM_OTLP_ENDPOINT", "https://siem.example.com/v1/logs")
    monkeypatch.setenv("AGENTFACTOR_SIEM_OTLP_TOKEN", "export-token")
    exporter = SecurityAuditExporter(httpx.Client(transport=httpx.MockTransport(handler)))

    first = exporter.export()
    second = exporter.export()

    assert first["exported"] == 1
    assert second == {"exported": 0, "status": "idle"}
    payload = captured[0].content.decode("utf-8")
    assert "must-not-be-exported" not in payload
    assert "export-token" not in payload
    assert captured[0].headers["idempotency-key"].startswith("sha256:")
