import json

import pytest

from agentfactor.mcp_server.tools import approval, identity


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


@pytest.mark.asyncio
async def test_identity_session_status_uses_session_detail(monkeypatch):
    calls = []

    async def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/terminals/worker-1":
            return {
                "id": "worker-1",
                "session_name": "session-a",
                "window_name": "worker-developer-codex",
                "provider": "codex",
                "agent_profile": "developer",
                "status": "READY",
            }
        if path == "/sessions/session-a":
            return {
                "name": "session-a",
                "terminals": [
                    {
                        "id": "supervisor-1",
                        "window_name": "supervisor-conductor-claude_code",
                        "agent_profile": "conductor",
                        "status": "READY",
                    },
                    {
                        "id": "worker-1",
                        "window_name": "worker-developer-codex",
                        "agent_profile": "developer",
                        "status": "READY",
                    },
                ],
            }
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(identity, "terminal_id", lambda: "worker-1")
    monkeypatch.setattr(identity, "request", fake_request)

    mcp = FakeMCP()
    identity.register(mcp)
    result = await mcp.tools["session_status"]()

    assert calls == [
        ("GET", "/terminals/worker-1", None),
        ("GET", "/sessions/session-a", None),
    ]
    assert result[0]["role"] == "supervisor"
    assert result[1]["role"] == "worker"


@pytest.mark.asyncio
async def test_approval_request_finds_supervisor_and_posts_api_contract(monkeypatch):
    calls = []

    async def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/terminals/worker-1":
            return {"id": "worker-1", "session_name": "session-a", "window_name": "worker-dev-codex"}
        if path == "/sessions/session-a":
            return {
                "name": "session-a",
                "terminals": [
                    {"id": "supervisor-1", "window_name": "supervisor-conductor-claude_code"},
                    {"id": "worker-1", "window_name": "worker-dev-codex"},
                ],
            }
        if path == "/approvals":
            return {"id": 7, **payload}
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(approval, "terminal_id", lambda: "worker-1")
    monkeypatch.setattr(approval, "request", fake_request)

    mcp = FakeMCP()
    approval.register(mcp)
    result = await mcp.tools["request_approval"]("rm -rf tmp", {"risk": "high"})

    assert result["supervisor_id"] == "supervisor-1"
    post = calls[-1]
    assert post[0:2] == ("POST", "/approvals")
    assert post[2]["metadata_payload"] == json.dumps({"risk": "high"})


@pytest.mark.asyncio
async def test_approval_tools_filter_pending_and_send_deny_body(monkeypatch):
    calls = []

    async def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/approvals?status_filter=PENDING":
            return [
                {"id": 1, "supervisor_id": "supervisor-1", "status": "PENDING"},
                {"id": 2, "supervisor_id": "other", "status": "PENDING"},
            ]
        if path == "/approvals/1/deny":
            return {"id": 1, "status": "DENIED"}
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(approval, "terminal_id", lambda: "supervisor-1")
    monkeypatch.setattr(approval, "request", fake_request)

    mcp = FakeMCP()
    approval.register(mcp)

    pending = await mcp.tools["list_pending_approvals"]()
    denied = await mcp.tools["decide_approval"]("1", False)

    assert pending == [{"id": 1, "supervisor_id": "supervisor-1", "status": "PENDING"}]
    assert denied["status"] == "DENIED"
    assert calls[-1] == ("POST", "/approvals/1/deny", {"reason": None})
