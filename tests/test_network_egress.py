import socket

import pytest

from agentfactor.clients.database import SecurityExecutionResult, session_scope
from agentfactor.security.executor import NativeExecutorError, NativeExecutorService
from agentfactor.security.gateway import SecurityGatewayService
from agentfactor.security.network import NetworkEgressService
from agentfactor.security.policy import SecurityPolicyService
from agentfactor.security.schemas import (
    NativeNetworkRequest,
    NativeSessionCreateRequest,
    SecurityPolicyCreateRequest,
    SecurityPolicyRevisionCreateRequest,
)


def _service(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "gateway.json"
    policy = SecurityPolicyService(config_path)
    policy.create_policy(SecurityPolicyCreateRequest(id="network", display_name="Network"))
    revision = policy.create_revision("network", SecurityPolicyRevisionCreateRequest.model_validate({
        "rules": [{
            "id": "network-request",
            "effect": "record",
            "principals": ["native-agent"],
            "surfaces": ["network"],
            "operations": ["request"],
            "resource_prefixes": ["https://api.example.com:443/"],
        }],
        "network_routes": [{
            "id": "example-api",
            "schemes": ["https"],
            "host_patterns": ["api.example.com"],
            "ports": [443],
        }],
    }))
    policy.publish("network", revision["id"])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])
    network = NetworkEgressService()
    executor = NativeExecutorService(SecurityGatewayService(config_path), network=network)
    native_session = executor.create_session(NativeSessionCreateRequest.model_validate({
        "principal": {"id": "native-agent"},
        "project_id": "project",
        "workspace_path": str(workspace),
        "agent": {"name": "network-test"},
    }))
    return executor, network, native_session


def test_network_executor_pins_validated_ip_and_records_execution(tmp_path, monkeypatch):
    executor, network, native_session = _service(tmp_path, monkeypatch)
    observed = {}

    def request_once(method, url, host, port, address, headers, body, timeout):
        observed.update({"host": host, "address": address, "url": url})
        return 200, {"content-type": "text/plain"}, b"external value"

    monkeypatch.setattr(network, "_request_once", request_once)
    value = executor.network_request(NativeNetworkRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        method="GET",
        url="https://api.example.com/v1/items",
    ))

    assert observed == {
        "host": "api.example.com",
        "address": "93.184.216.34",
        "url": "https://api.example.com:443/v1/items",
    }
    assert value["result"]["body"] == "external value"
    assert value["result_labels"] == ["untrusted-content"]
    with session_scope() as db:
        assert db.query(SecurityExecutionResult).count() == 1


def test_network_unknown_relay_and_private_resolution_are_blocked(tmp_path, monkeypatch):
    executor, network, native_session = _service(tmp_path, monkeypatch)
    request = NativeNetworkRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        url="https://relay.example.net/",
    )
    with pytest.raises(NativeExecutorError, match="network_route_blocked"):
        executor.network_request(request)

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    request.url = "https://api.example.com/"
    with pytest.raises(NativeExecutorError, match="network_private_ip_blocked"):
        executor.network_request(request)


def test_network_redirect_target_is_reauthorized(tmp_path, monkeypatch):
    executor, network, native_session = _service(tmp_path, monkeypatch)
    monkeypatch.setattr(
        network,
        "_request_once",
        lambda *_args, **_kwargs: (302, {"location": "https://relay.example.net/collect"}, b""),
    )

    with pytest.raises(NativeExecutorError, match="network_route_blocked"):
        executor.network_request(NativeNetworkRequest(
            session_id=native_session["id"],
            token=native_session["token"],
            url="https://api.example.com/redirect",
        ))
