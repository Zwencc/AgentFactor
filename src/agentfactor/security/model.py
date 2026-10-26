"""OpenAI-compatible model egress with policy routing and brokered credentials."""

from __future__ import annotations

import json
from typing import Any, Callable

from agentfactor.security.credentials import CredentialBroker
from agentfactor.security.dataflow import DataFlowService
from agentfactor.security.network import NetworkEgressService


class ModelEgressError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class ModelEgressService:
    def __init__(
        self,
        dataflow: DataFlowService | None = None,
        network: NetworkEgressService | None = None,
        credentials: CredentialBroker | None = None,
    ) -> None:
        self.dataflow = dataflow or DataFlowService()
        self.network = network or NetworkEgressService(self.dataflow)
        self.credentials = credentials or CredentialBroker(self.dataflow)

    def complete(
        self,
        *,
        session_id: str,
        provider_id: str,
        model: str,
        messages: list[dict[str, Any]],
        parameters: dict[str, Any],
        timeout_seconds: int,
        authorize: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
        record: Callable[[dict[str, Any], dict[str, Any], bool, dict[str, Any], int], None],
    ) -> dict[str, Any]:
        document, _policy_digest = self.dataflow.active_document()
        provider = next(
            (item for item in document.get("model_providers", []) if item.get("id") == provider_id),
            None,
        )
        if provider is None or model not in provider.get("models", []):
            raise ModelEgressError("model_route_blocked", 403)
        resource = f"model://{provider_id}/{model}"
        secret, lease_id = self.credentials.checkout(
            provider.get("credential_id", ""), session_id, "model", resource
        )
        payload = {"model": model, "messages": messages, **parameters}
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ModelEgressError("model_request_invalid") from exc
        if len(body.encode("utf-8")) > 1 << 20:
            raise ModelEgressError("model_request_too_large", 413)
        upstream = provider.get("base_url", "").rstrip("/") + "/chat/completions"
        value = self.network.request(
            session_id=session_id,
            method="POST",
            url=upstream,
            headers={"authorization": f"Bearer {secret}", "content-type": "application/json"},
            body=body,
            timeout_seconds=timeout_seconds,
            authorize=lambda actual_url, params: authorize(resource, {**params, "upstream_url": actual_url}),
            record=record,
            surface="model",
            allow_authorization=True,
            egress_destination=resource,
        )
        try:
            response = json.loads(value["body"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ModelEgressError("model_response_invalid", 502) from exc
        return {
            "response": response,
            "provider_id": provider_id,
            "model": model,
            "credential_lease_id": lease_id,
            "hops": value["hops"],
            "data_flow": value["data_flow"],
        }
