import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agentfactor.api import main as api_main
from agentfactor.clients.database import SecurityExecutionGrant, session_scope
from agentfactor.security import api as security_api
from agentfactor.security.gateway import SecurityGatewayError, SecurityGatewayService
from agentfactor.security.schemas import CanonicalSecurityAction, MCPCallRequest


def gateway_config(tmp_path, *, effect="record"):
    path = tmp_path / "security-gateway.json"
    path.write_text(json.dumps({
        "policy_version": "v1",
        "default_effect": "block",
        "rules": [{
            "id": "workspace-shell",
            "effect": effect,
            "principals": ["agent-1"],
            "surfaces": ["shell"],
            "operations": ["exec"],
            "resource_prefixes": ["command://"],
        }],
        "mcp_servers": {},
    }))
    return path


def action(**overrides):
    value = {
        "principal": {"id": "agent-1", "authn": "local"},
        "endpoint_id": "endpoint-1",
        "agent": {"name": "codex", "version": "1"},
        "integration_mode": "gateway",
        "context": {
            "project_id": "project-1",
            "session_id": "session-1",
            "cwd": "/workspace/project",
            "tool_call_id": "call-1",
        },
        "action": {
            "surface": "shell",
            "operation": "exec",
            "resource": "command://scripts/deploy.sh",
            "params": {"argv": ["staging"]},
        },
    }
    for key, item in overrides.items():
        value[key] = item
    return CanonicalSecurityAction.model_validate(value)


def test_action_grant_is_bound_to_action_and_single_use(tmp_path):
    service = SecurityGatewayService(gateway_config(tmp_path))
    original = action()
    decision = service.evaluate(original)

    assert decision["effect"] == "record"
    assert decision["allowed"] is True
    assert decision["enforced"] is False

    changed = original.model_copy(deep=True)
    changed.action.params["argv"] = ["production"]
    with pytest.raises(SecurityGatewayError, match="grant_invalid_expired_or_consumed"):
        service.consume(decision["execution_grant"], changed)

    consumed = service.consume(decision["execution_grant"], original)
    assert consumed["authorized"] is True
    with pytest.raises(SecurityGatewayError, match="grant_invalid_expired_or_consumed"):
        service.consume(decision["execution_grant"], original)


@pytest.mark.parametrize("field", ["cwd", "resource"])
def test_action_grant_rejects_changed_execution_object(tmp_path, field):
    service = SecurityGatewayService(gateway_config(tmp_path))
    original = action()
    decision = service.evaluate(original)
    changed = original.model_copy(deep=True)
    if field == "cwd":
        changed.context.cwd = "/workspace/other"
    else:
        changed.action.resource = "command://scripts/other.sh"

    with pytest.raises(SecurityGatewayError, match="grant_invalid_expired_or_consumed"):
        service.consume(decision["execution_grant"], changed)


def test_policy_change_invalidates_grant(tmp_path):
    path = gateway_config(tmp_path)
    service = SecurityGatewayService(path)
    original = action()
    decision = service.evaluate(original)
    config = json.loads(path.read_text())
    config["policy_version"] = "v2"
    path.write_text(json.dumps(config))

    with pytest.raises(SecurityGatewayError, match="grant_invalid_expired_or_consumed"):
        service.consume(decision["execution_grant"], original)


def test_expired_grant_is_rejected(tmp_path):
    service = SecurityGatewayService(gateway_config(tmp_path))
    original = action()
    decision = service.evaluate(original)
    with session_scope() as db:
        grant = db.get(SecurityExecutionGrant, decision["grant_id"])
        grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(SecurityGatewayError, match="grant_invalid_expired_or_consumed"):
        service.consume(decision["execution_grant"], original)


def test_concurrent_grant_consumption_allows_exactly_one_caller(tmp_path):
    service = SecurityGatewayService(gateway_config(tmp_path))
    original = action()
    decision = service.evaluate(original)

    def consume():
        try:
            service.consume(decision["execution_grant"], original)
            return "authorized"
        except SecurityGatewayError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: consume(), range(2)))

    assert sorted(outcomes) == ["authorized", "rejected"]


def test_filesystem_prefix_does_not_match_sibling_path(tmp_path):
    config_path = tmp_path / "security-gateway.json"
    config_path.write_text(json.dumps({
        "policy_version": "v1",
        "default_effect": "block",
        "rules": [{
            "id": "workspace-files",
            "effect": "record",
            "surfaces": ["filesystem"],
            "operations": ["write"],
            "resource_prefixes": ["/workspace/project"],
        }],
        "mcp_servers": {},
    }))
    service = SecurityGatewayService(config_path)
    value = action()
    value.action.surface = "filesystem"
    value.action.operation = "write"
    value.action.resource = "/workspace/project-escape/file.txt"

    assert service.evaluate(value)["effect"] == "block"


def test_three_state_effects_create_only_expected_alerts_and_grants(tmp_path):
    remind = SecurityGatewayService(gateway_config(tmp_path, effect="remind")).evaluate(action())
    assert remind["allowed"] is True
    assert "execution_grant" in remind

    blocked_action = action()
    blocked_action.principal.id = "unknown-agent"
    service = SecurityGatewayService(gateway_config(tmp_path, effect="record"))
    blocked = service.evaluate(blocked_action)
    assert blocked["effect"] == "block"
    assert blocked["enforced"] is True
    assert "execution_grant" not in blocked
    alerts = service.alerts()
    assert {item["effect"] for item in alerts} == {"remind", "block"}


class FakeTool:
    def __init__(self, name, schema):
        self.name = name
        self.schema = schema

    def model_dump(self, **_kwargs):
        return {"name": self.name, "inputSchema": self.schema}


class FakeResult:
    def model_dump(self, **_kwargs):
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


class FakeClient:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def list_tools(self):
        return [FakeTool("hello", copy.deepcopy(self.schema))]

    async def call_tool(self, *_args, **_kwargs):
        return FakeResult()


def mcp_config(tmp_path):
    path = tmp_path / "security-gateway.json"
    path.write_text(json.dumps({
        "policy_version": "v1",
        "default_effect": "block",
        "rules": [],
        "mcp_servers": {
            "demo": {
                "display_name": "Demo",
                "command": "demo-server",
                "args": [],
                "tools": {"hello": {"effect": "remind"}},
            },
        },
    }))
    return path


def mcp_request(arguments):
    return MCPCallRequest.model_validate({
        "principal": {"id": "agent-1"},
        "endpoint_id": "endpoint-1",
        "agent": {"name": "codex"},
        "project_id": "project-1",
        "session_id": "session-1",
        "cwd": "/workspace/project",
        "arguments": arguments,
    })


@pytest.mark.asyncio
async def test_mcp_gateway_allows_listed_tool_and_rejects_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr("agentfactor.security.gateway.Client", FakeClient)
    service = SecurityGatewayService(mcp_config(tmp_path))

    result = await service.call_mcp("demo", "hello", mcp_request({"name": "z"}))
    assert result["decision"]["effect"] == "remind"
    assert result["result"]["isError"] is False
    assert service.mcp_inventory()[0]["tools"][0]["status"] == "allowed"

    with pytest.raises(SecurityGatewayError, match="mcp_tool_unknown"):
        await service.call_mcp("demo", "other", mcp_request({}))


@pytest.mark.asyncio
async def test_mcp_gateway_rejects_invalid_arguments_and_schema_drift(monkeypatch, tmp_path):
    monkeypatch.setattr("agentfactor.security.gateway.Client", FakeClient)
    path = mcp_config(tmp_path)
    service = SecurityGatewayService(path)
    await service.discover_mcp()

    with pytest.raises(SecurityGatewayError, match="mcp_arguments_invalid"):
        await service.call_mcp("demo", "hello", mcp_request({"extra": True}))

    FakeClient.schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    try:
        with pytest.raises(SecurityGatewayError, match="mcp_schema_changed"):
            await service.call_mcp("demo", "hello", mcp_request({"count": 1}))
        with pytest.raises(SecurityGatewayError, match="mcp_schema_changed"):
            await service.call_mcp("demo", "hello", mcp_request({"count": 1}))
        schema_digest = service.mcp_inventory()[0]["tools"][0]["schema_digest"]
        config = json.loads(path.read_text())
        config["mcp_servers"]["demo"]["tools"]["hello"]["schema_digest"] = schema_digest
        path.write_text(json.dumps(config))
        result = await service.call_mcp("demo", "hello", mcp_request({"count": 1}))
        assert result["decision"]["effect"] == "remind"
    finally:
        FakeClient.schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}


@pytest.mark.asyncio
async def test_agent_mcp_inventory_requires_an_action_policy(monkeypatch, tmp_path):
    monkeypatch.setattr("agentfactor.security.gateway.Client", FakeClient)
    path = mcp_config(tmp_path)
    service = SecurityGatewayService(path)
    request = {
        "principal": {"id": "agent-1"},
        "endpoint_id": "endpoint-1",
        "agent": {"name": "codex"},
        "project_id": "project-1",
        "session_id": "session-1",
        "cwd": "/workspace/project",
    }
    from agentfactor.security.schemas import MCPInventoryRequest

    with pytest.raises(SecurityGatewayError, match="mcp_inventory_blocked"):
        await service.list_mcp_for_agent(MCPInventoryRequest.model_validate(request))

    config = json.loads(path.read_text())
    config["rules"] = [{
        "id": "inventory",
        "effect": "record",
        "principals": ["agent-1"],
        "surfaces": ["mcp"],
        "operations": ["list"],
        "resource_prefixes": ["mcp://inventory"],
    }]
    path.write_text(json.dumps(config))
    result = await service.list_mcp_for_agent(MCPInventoryRequest.model_validate(request))
    assert result["decision"]["effect"] == "record"
    assert result["servers"][0]["tools"][0]["name"] == "hello"


def test_security_gateway_api_exposes_three_state_decisions_and_consumption(api_client, tmp_path):
    service = SecurityGatewayService(gateway_config(tmp_path, effect="remind"))
    original = api_main.app.dependency_overrides.copy()
    api_main.app.dependency_overrides[security_api.get_security_gateway_service] = lambda: service
    try:
        payload = action().model_dump(mode="json")
        evaluated = api_client.post("/security/v1alpha1/actions/evaluate", json=payload)
        assert evaluated.status_code == 200
        decision = evaluated.json()
        assert decision["effect"] == "remind"

        consumed = api_client.post("/security/v1alpha1/execution-grants/consume", json={
            "token": decision["execution_grant"],
            "action": payload,
        })
        assert consumed.status_code == 200
        assert consumed.json()["authorized"] is True
        assert api_client.get("/security/v1alpha1/action-decisions").json()[0]["effect"] == "remind"
        assert api_client.get("/security/v1alpha1/alerts").json()[0]["effect"] == "remind"
    finally:
        api_main.app.dependency_overrides = original
