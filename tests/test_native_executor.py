import json
import sys

import pytest

from agentfactor.api import main as api_main
from agentfactor.clients.database import SecurityExecutionResult, SecurityNativeSession, session_scope
from agentfactor.security import api as security_api
from agentfactor.security.executor import NativeExecutorError, NativeExecutorService
from agentfactor.security.gateway import SecurityGatewayService
from agentfactor.security.schemas import (
    NativeFilesystemRequest,
    NativeProcessRequest,
    NativeSessionCreateRequest,
)


def _service(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "gateway.json"
    config.write_text(json.dumps({
        "policy_version": "native-v1",
        "default_effect": "block",
        "rules": [
            {
                "id": "native-files",
                "effect": "record",
                "principals": ["native-agent"],
                "surfaces": ["filesystem"],
                "operations": ["read_text", "write_text", "mkdir", "delete_file", "list"],
                "resource_prefixes": [str(workspace)],
            },
            {
                "id": "native-process",
                "effect": "record",
                "principals": ["native-agent"],
                "surfaces": ["shell"],
                "operations": ["exec"],
                "resource_prefixes": ["command://"],
            },
        ],
        "mcp_servers": {},
    }))
    return workspace, NativeExecutorService(SecurityGatewayService(config))


def _session(service, workspace):
    return service.create_session(NativeSessionCreateRequest.model_validate({
        "principal": {"id": "native-agent"},
        "project_id": "project",
        "workspace_path": str(workspace),
        "agent": {"name": "example-agent", "version": "1"},
    }))


def test_native_filesystem_executor_is_workspace_scoped_and_evidenced(tmp_path):
    workspace, service = _service(tmp_path)
    session = _session(service, workspace)
    assert session["assurance"] == "OBSERVED"

    written = service.filesystem(NativeFilesystemRequest(
        session_id=session["id"],
        token=session["token"],
        operation="write_text",
        path="result.txt",
        content="safe output",
    ))
    assert written["assurance"] == "EXECUTOR_ENFORCED"
    assert (workspace / "result.txt").read_text() == "safe output"

    read = service.filesystem(NativeFilesystemRequest(
        session_id=session["id"],
        token=session["token"],
        operation="read_text",
        path="result.txt",
    ))
    assert read["result"]["content"] == "safe output"
    with session_scope() as db:
        assert db.query(SecurityExecutionResult).count() == 2

    with pytest.raises(NativeExecutorError, match="native_path_outside_workspace"):
        service.filesystem(NativeFilesystemRequest(
            session_id=session["id"],
            token=session["token"],
            operation="write_text",
            path="../escape.txt",
            content="blocked",
        ))
    assert not (tmp_path / "escape.txt").exists()


def test_native_process_executor_uses_argv_without_shell(tmp_path):
    workspace, service = _service(tmp_path)
    session = _session(service, workspace)

    value = service.process(NativeProcessRequest(
        session_id=session["id"],
        token=session["token"],
        argv=[sys.executable, "-c", "print('executor-ok')"],
    ))

    assert value["assurance"] == "EXECUTOR_ENFORCED"
    assert value["result"]["returncode"] == 0
    assert value["result"]["stdout"].strip() == "executor-ok"


def test_native_session_token_is_required_and_bypass_is_not_native_enforced(tmp_path):
    workspace, service = _service(tmp_path)
    session = _session(service, workspace)

    with pytest.raises(NativeExecutorError, match="native_session_unauthorized"):
        service.filesystem(NativeFilesystemRequest(
            session_id=session["id"],
            token="wrong-token",
            operation="list",
            path=".",
        ))

    (workspace / "bypass.txt").write_text("direct write")
    with session_scope() as db:
        stored = db.get(SecurityNativeSession, session["id"])
        assert stored.assurance == "OBSERVED"


def test_native_executor_api_never_returns_token_after_session_creation(api_client, tmp_path):
    workspace, service = _service(tmp_path)
    original = api_main.app.dependency_overrides.copy()
    api_main.app.dependency_overrides[security_api.get_native_executor_service] = lambda: service
    try:
        created = api_client.post("/security/v1alpha1/native/sessions", json={
            "principal": {"id": "native-agent"},
            "project_id": "project",
            "workspace_path": str(workspace),
            "agent": {"name": "example-agent"},
        })
        assert created.status_code == 200
        session = created.json()
        executed = api_client.post("/security/v1alpha1/native/filesystem", json={
            "session_id": session["id"],
            "token": session["token"],
            "operation": "list",
            "path": ".",
        })
        assert executed.status_code == 200
        assert "token" not in executed.json()
    finally:
        api_main.app.dependency_overrides = original
