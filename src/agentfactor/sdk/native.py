"""Python Native SDK backed by the AgentFactor controlled Executor."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


class NativeSDKError(RuntimeError):
    """The Native control plane rejected or failed an SDK operation."""


class AgentFactorNative:
    def __init__(
        self,
        *,
        principal_id: str,
        project_id: str,
        workspace: str | Path,
        agent_name: str,
        agent_version: str | None = None,
        api_base: str | None = None,
        timeout_seconds: float = 130.0,
    ) -> None:
        self.principal_id = principal_id
        self.project_id = project_id
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.agent_name = agent_name
        self.agent_version = agent_version
        self.api_base = (api_base or os.getenv("AGENTFACTOR_API_BASE") or "http://127.0.0.1:9889").rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds)
        self._session_id: str | None = None
        self._token: str | None = None
        self.assurance: str | None = None

    def __enter__(self) -> AgentFactorNative:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def start(self) -> dict[str, Any]:
        if self._session_id is not None:
            raise NativeSDKError("Native session is already active")
        value = self._request("POST", "/security/v1alpha1/native/sessions", {
            "principal": {"id": self.principal_id, "authn": "native-sdk"},
            "project_id": self.project_id,
            "workspace_path": self.workspace,
            "agent": {"name": self.agent_name, "version": self.agent_version},
        })
        self._session_id = value["id"]
        self._token = value["token"]
        self.assurance = value["assurance"]
        return {key: item for key, item in value.items() if key != "token"}

    def close(self) -> None:
        if self._session_id is None or self._token is None:
            self._client.close()
            return
        try:
            self._request(
                "POST",
                f"/security/v1alpha1/native/sessions/{self._session_id}/close",
                {"token": self._token},
            )
        finally:
            self._session_id = None
            self._token = None
            self._client.close()

    def read_text(self, path: str) -> str:
        return self._filesystem("read_text", path)["result"]["content"]

    def write_text(self, path: str, content: str) -> dict[str, Any]:
        return self._filesystem("write_text", path, content)

    def mkdir(self, path: str) -> dict[str, Any]:
        return self._filesystem("mkdir", path)

    def delete_file(self, path: str) -> dict[str, Any]:
        return self._filesystem("delete_file", path)

    def list_directory(self, path: str = ".") -> list[str]:
        return self._filesystem("list", path)["result"]["entries"]

    def run(
        self,
        argv: list[str],
        *,
        cwd: str = ".",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        session_id, token = self._credentials()
        return self._request("POST", "/security/v1alpha1/native/process", {
            "session_id": session_id,
            "token": token,
            "argv": argv,
            "cwd": cwd,
            "timeout_seconds": timeout_seconds,
        })

    def call_mcp(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id, token = self._credentials()
        return self._request("POST", "/security/v1alpha1/native/mcp", {
            "session_id": session_id,
            "token": token,
            "server_id": server_id,
            "tool_name": tool_name,
            "arguments": arguments or {},
        })

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        session_id, token = self._credentials()
        return self._request("POST", "/security/v1alpha1/native/network", {
            "session_id": session_id,
            "token": token,
            "method": method,
            "url": url,
            "headers": headers or {},
            "body": body,
            "timeout_seconds": timeout_seconds,
        })

    def complete(
        self,
        provider_id: str,
        model: str,
        messages: list[dict[str, Any]],
        *,
        parameters: dict[str, Any] | None = None,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        session_id, token = self._credentials()
        return self._request("POST", "/security/v1alpha1/native/model", {
            "session_id": session_id,
            "token": token,
            "provider_id": provider_id,
            "model": model,
            "messages": messages,
            "parameters": parameters or {},
            "timeout_seconds": timeout_seconds,
        })

    def declassify(self, rule_id: str, purpose: str) -> dict[str, Any]:
        session_id, token = self._credentials()
        return self._request("POST", "/security/v1alpha1/native/declassify", {
            "session_id": session_id,
            "token": token,
            "rule_id": rule_id,
            "purpose": purpose,
        })

    def _filesystem(self, operation: str, path: str, content: str | None = None) -> dict[str, Any]:
        session_id, token = self._credentials()
        return self._request("POST", "/security/v1alpha1/native/filesystem", {
            "session_id": session_id,
            "token": token,
            "operation": operation,
            "path": path,
            "content": content,
        })

    def _credentials(self) -> tuple[str, str]:
        if self._session_id is None or self._token is None:
            raise NativeSDKError("Native session has not been started")
        return self._session_id, self._token

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.request(method, f"{self.api_base}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise NativeSDKError("AgentFactor API is unavailable") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "native_request_failed")
            except ValueError:
                detail = "native_request_failed"
            raise NativeSDKError(str(detail))
        value = response.json()
        if not isinstance(value, dict):
            raise NativeSDKError("AgentFactor API returned an invalid response")
        return value
