"""Small, bounded client for ACR's private Unix socket protocol."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from agentfactor.security.schemas import (
    ACRCapabilities,
    ACRCanary,
    ACRCoverage,
    ACREventPage,
    ACRPosture,
    CONTROL_PROTOCOL_VERSION,
)


MAXIMUM_RESPONSE_BYTES = 8 << 20
ModelT = TypeVar("ModelT", bound=BaseModel)


class ACRClientError(RuntimeError):
    """ACR could not serve a valid control-protocol response."""


class ACRUnavailableError(ACRClientError):
    """The configured ACR daemon socket is not reachable."""


class ACRIncompatibleError(ACRClientError):
    """The daemon answered with an unsupported or malformed protocol."""


def default_socket_path() -> Path:
    configured = os.getenv("AGENTFACTOR_ACR_SOCKET") or os.getenv("ACR_DAEMON_SOCKET")
    if configured:
        return Path(configured)
    runtime_directory = os.getenv("XDG_RUNTIME_DIR")
    if runtime_directory:
        return Path(runtime_directory) / "acr" / "daemon.sock"
    return Path.home() / ".acr" / "run" / "daemon.sock"


class ACRClient:
    def __init__(self, socket_path: Path | None = None, timeout_seconds: float = 2.0) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.timeout_seconds = timeout_seconds

    def capabilities(self) -> ACRCapabilities:
        return self._call("capabilities", {}, ACRCapabilities)

    def posture(self) -> ACRPosture:
        return self._call("posture", {}, ACRPosture)

    def events(self, workspace_id: str, after_sequence: int, limit: int) -> ACREventPage:
        return self._call(
            "events",
            {
                "workspace_id": workspace_id,
                "after_sequence": after_sequence,
                "limit": limit,
            },
            ACREventPage,
        )

    def coverage(self, workspace_id: str) -> ACRCoverage:
        return self._call("coverage", {"workspace_id": workspace_id}, ACRCoverage)

    def canary(self, workspace_id: str) -> ACRCanary:
        return self._call("canary", {"workspace_id": workspace_id}, ACRCanary)

    def _call(self, operation: str, params: dict[str, Any], model: type[ModelT]) -> ModelT:
        request = json.dumps(
            {
                "command": "control",
                "protocol_version": CONTROL_PROTOCOL_VERSION,
                "operation": operation,
                "params": params,
            },
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(request)
                response = self._read_response(connection)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as exc:
            raise ACRUnavailableError(f"ACR daemon is unavailable at {self.socket_path}") from exc

        try:
            envelope = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ACRIncompatibleError("ACR returned malformed JSON") from exc
        if not isinstance(envelope, dict):
            raise ACRIncompatibleError("ACR response must be an object")
        if envelope.get("error"):
            raise ACRClientError(str(envelope["error"]))
        if envelope.get("protocol_version") != CONTROL_PROTOCOL_VERSION:
            raise ACRIncompatibleError("ACR returned an unsupported control protocol")
        if "result" not in envelope:
            raise ACRIncompatibleError("ACR response omitted result")
        try:
            return model.model_validate(envelope["result"])
        except ValidationError as exc:
            raise ACRIncompatibleError(f"ACR returned an invalid {operation} result") from exc

    def _read_response(self, connection: socket.socket) -> bytes:
        data = bytearray()
        while len(data) <= MAXIMUM_RESPONSE_BYTES:
            chunk = connection.recv(min(65536, MAXIMUM_RESPONSE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
        if len(data) > MAXIMUM_RESPONSE_BYTES:
            raise ACRIncompatibleError("ACR response exceeded the size limit")
        line, separator, trailing = bytes(data).partition(b"\n")
        if not separator:
            raise ACRIncompatibleError("ACR response was not newline terminated")
        if trailing.strip():
            raise ACRIncompatibleError("ACR returned multiple response values")
        return line
