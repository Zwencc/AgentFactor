import json
from unittest.mock import AsyncMock

import pytest

from agentfactor.clients.database import SecurityDeclassification, session_scope
from agentfactor.security.dataflow import DataFlowError, DataFlowService
from agentfactor.security.executor import NativeExecutorError, NativeExecutorService
from agentfactor.security.gateway import SecurityGatewayService
from agentfactor.security.policy import SecurityPolicyService
from agentfactor.security.schemas import (
    NativeDeclassificationRequest,
    NativeFilesystemRequest,
    NativeMCPRequest,
    NativeSessionCreateRequest,
    SecurityPolicyCreateRequest,
    SecurityPolicyRevisionCreateRequest,
)


def _configured_service(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = workspace / "secret.txt"
    secret.write_text("credential-value")
    config_path = tmp_path / "gateway.json"
    policy = SecurityPolicyService(config_path)
    policy.create_policy(SecurityPolicyCreateRequest(id="data-policy", display_name="Data policy"))
    revision = policy.create_revision("data-policy", SecurityPolicyRevisionCreateRequest.model_validate({
        "rules": [{
            "id": "native-files",
            "effect": "record",
            "principals": ["native-agent"],
            "surfaces": ["filesystem"],
            "operations": ["read_text"],
            "resource_prefixes": [str(workspace)],
        }],
        "data_rules": [{
            "id": "workspace-secrets",
            "resource_prefixes": [str(secret)],
            "labels": ["credential", "source-code"],
        }],
        "egress_rules": [{
            "id": "approved-model",
            "surfaces": ["model"],
            "destination_prefixes": ["model://approved/"],
            "allowed_labels": ["source-code"],
        }, {
            "id": "approved-mcp",
            "surfaces": ["mcp"],
            "destination_prefixes": ["mcp://approved/"],
            "allowed_labels": ["credential", "source-code"],
        }],
        "declassification_rules": [{
            "id": "remove-credential",
            "purpose": "redacted-by-reviewed-transform",
            "remove_labels": ["credential"],
        }],
    }))
    policy.publish("data-policy", revision["id"])
    dataflow = DataFlowService()
    executor = NativeExecutorService(SecurityGatewayService(config_path), dataflow)
    session = executor.create_session(NativeSessionCreateRequest.model_validate({
        "principal": {"id": "native-agent"},
        "project_id": "project",
        "workspace_path": str(workspace),
        "agent": {"name": "native-test"},
    }))
    return secret, executor, dataflow, session


def test_sensitive_read_taints_session_and_blocks_unapproved_egress(tmp_path):
    secret, executor, dataflow, native_session = _configured_service(tmp_path)

    result = executor.filesystem(NativeFilesystemRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        operation="read_text",
        path=str(secret),
    ))

    assert result["result"]["labels"] == ["credential", "source-code"]
    assert result["result"]["session_taint"] == ["credential", "source-code"]
    with pytest.raises(DataFlowError, match="sensitive_egress_blocked"):
        dataflow.authorize_egress(native_session["id"], "network", "https://example.com/")
    with pytest.raises(DataFlowError, match="sensitive_egress_blocked"):
        dataflow.authorize_egress(native_session["id"], "model", "model://approved/chat")


def test_declassification_requires_exact_published_rule_and_records_evidence(tmp_path):
    secret, executor, dataflow, native_session = _configured_service(tmp_path)
    executor.filesystem(NativeFilesystemRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        operation="read_text",
        path=str(secret),
    ))

    with pytest.raises(NativeExecutorError, match="declassification_not_authorized"):
        executor.declassify(NativeDeclassificationRequest(
            session_id=native_session["id"],
            token=native_session["token"],
            rule_id="remove-credential",
            purpose="agent-says-safe",
        ))
    value = executor.declassify(NativeDeclassificationRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        rule_id="remove-credential",
        purpose="redacted-by-reviewed-transform",
    ))

    assert value["removed_labels"] == ["credential"]
    assert dataflow.session_labels(native_session["id"]) == ["source-code"]
    allowed = dataflow.authorize_egress(native_session["id"], "model", "model://approved/chat")
    assert allowed["rule_ids"] == ["approved-model"]
    with session_scope() as db:
        evidence = db.query(SecurityDeclassification).one()
        assert json.loads(evidence.removed_labels) == ["credential"]
        assert evidence.purpose_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_native_mcp_checks_data_egress_and_taints_untrusted_result(tmp_path):
    secret, executor, dataflow, native_session = _configured_service(tmp_path)
    executor.filesystem(NativeFilesystemRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        operation="read_text",
        path=str(secret),
    ))
    executor.gateway.call_mcp = AsyncMock(return_value={"decision": {"effect": "record"}, "result": {}})

    with pytest.raises(NativeExecutorError, match="sensitive_egress_blocked"):
        await executor.mcp(NativeMCPRequest(
            session_id=native_session["id"],
            token=native_session["token"],
            server_id="unknown",
            tool_name="send",
            arguments={"value": "credential-value"},
        ))
    executor.gateway.call_mcp.assert_not_awaited()

    value = await executor.mcp(NativeMCPRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        server_id="approved",
        tool_name="send",
        arguments={"value": "credential-value"},
    ))
    assert value["assurance"] == "EXECUTOR_ENFORCED"
    assert value["data_flow"]["egress"]["rule_ids"] == ["approved-mcp"]
    assert value["session_taint"] == ["credential", "source-code", "untrusted-content"]
