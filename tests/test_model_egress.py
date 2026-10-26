import socket

from agentfactor.clients.database import SecurityCredentialLease, session_scope
from agentfactor.security.executor import NativeExecutorService
from agentfactor.security.gateway import SecurityGatewayService
from agentfactor.security.model import ModelEgressService
from agentfactor.security.network import NetworkEgressService
from agentfactor.security.policy import SecurityPolicyService
from agentfactor.security.schemas import (
    NativeModelRequest,
    NativeSessionCreateRequest,
    SecurityPolicyCreateRequest,
    SecurityPolicyRevisionCreateRequest,
)


def test_model_gateway_injects_brokered_credential_without_returning_it(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "gateway.json"
    policy = SecurityPolicyService(config_path)
    policy.create_policy(SecurityPolicyCreateRequest(id="model", display_name="Model"))
    revision = policy.create_revision("model", SecurityPolicyRevisionCreateRequest.model_validate({
        "rules": [{
            "id": "model-complete",
            "effect": "record",
            "principals": ["native-agent"],
            "surfaces": ["model"],
            "operations": ["complete"],
            "resource_prefixes": ["model://openai/"],
        }],
        "network_routes": [{
            "id": "model-api",
            "schemes": ["https"],
            "host_patterns": ["model.example.com"],
            "ports": [443],
        }],
        "credentials": [{
            "id": "model-key",
            "source_env": "AGENTFACTOR_TEST_MODEL_KEY",
            "allowed_surfaces": ["model"],
            "resource_prefixes": ["model://openai/"],
        }],
        "model_providers": [{
            "id": "openai",
            "base_url": "https://model.example.com/v1",
            "models": ["secure-model"],
            "credential_id": "model-key",
        }],
    }))
    policy.publish("model", revision["id"])
    monkeypatch.setenv("AGENTFACTOR_TEST_MODEL_KEY", "long-lived-provider-secret")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])
    network = NetworkEgressService()
    observed = {}

    def request_once(method, url, host, port, address, headers, body, timeout):
        observed.update({"authorization": headers["authorization"], "body": body})
        return 200, {"content-type": "application/json"}, b'{"choices":[{"message":{"content":"ok"}}]}'

    monkeypatch.setattr(network, "_request_once", request_once)
    model = ModelEgressService(network=network)
    executor = NativeExecutorService(SecurityGatewayService(config_path), network=network, model=model)
    native_session = executor.create_session(NativeSessionCreateRequest.model_validate({
        "principal": {"id": "native-agent"},
        "project_id": "project",
        "workspace_path": str(workspace),
        "agent": {"name": "model-test"},
    }))

    value = executor.model_request(NativeModelRequest(
        session_id=native_session["id"],
        token=native_session["token"],
        provider_id="openai",
        model="secure-model",
        messages=[{"role": "user", "content": "hello"}],
    ))

    assert observed["authorization"] == "Bearer long-lived-provider-secret"
    assert value["result"]["response"]["choices"][0]["message"]["content"] == "ok"
    assert "long-lived-provider-secret" not in str(value)
    with session_scope() as db:
        lease = db.query(SecurityCredentialLease).one()
        assert lease.status == "consumed"
        assert "long-lived-provider-secret" not in lease.secret_digest
