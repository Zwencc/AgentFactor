"""Minimal MCP server helpers for AgentFactor."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from agentfactor import constants

LOG = logging.getLogger(__name__)
API_BASE = "http://127.0.0.1:9889"


class MCPError(RuntimeError):
    """Raised when MCP helpers encounter an error."""


def _terminal_id() -> str:
    terminal_id = os.getenv(constants.TERMINAL_ENV_VAR)
    if not terminal_id:
        raise MCPError("Not running inside AgentFactor terminal.")
    return terminal_id


def _request(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{API_BASE}{path}"
    with httpx.Client(timeout=120) as client:
        response = client.request(method, url, json=payload)
    if response.status_code >= 400:
        raise MCPError(f"API error {response.status_code}: {response.text}")
    return response.json() if response.content else None


def submit_blueprint(toml_content: str) -> Dict[str, Any]:
    """Submit a generated work graph blueprint (TOML) to the conductor.

    Called by a planner agent after generating work items. The blueprint is
    validated and saved server-side; a human reviews and imports via the UI.
    """
    terminal_id = _terminal_id()
    return _request("POST", f"/work-graph/blueprint/{terminal_id}", {"toml_content": toml_content})


def send_message(receiver_id: str, message: str) -> Dict[str, Any]:
    """Queue a message for another terminal."""
    sender_id = _terminal_id()
    payload = {"sender_id": sender_id, "receiver_id": receiver_id, "message": message}
    return _request("POST", "/inbox", payload)


def handoff(
    session_name: str,
    provider: str,
    agent_profile: Optional[str],
    message: str,
    role: str = "worker",
) -> Dict[str, Any]:
    """Create a worker terminal and send it a message synchronously."""
    worker = _request(
        "POST",
        f"/sessions/{session_name}/terminals",
        {"provider": provider, "agent_profile": agent_profile, "role": role},
    )
    worker_id = worker["id"]
    _request("POST", f"/terminals/{worker_id}/input", {"message": message})
    # Returns worker metadata; caller can poll output as needed.
    return worker


def assign(
    session_name: str,
    provider: str,
    agent_profile: Optional[str],
    message: str,
    role: str = "worker",
) -> Dict[str, Any]:
    """Create a worker terminal asynchronously."""
    worker = _request(
        "POST",
        f"/sessions/{session_name}/terminals",
        {"provider": provider, "agent_profile": agent_profile, "role": role},
    )
    worker_id = worker["id"]
    send_message(worker_id, message)
    return worker


def request_approval(supervisor_id: str, command_text: str, metadata: Optional[str] = None) -> Dict[str, Any]:
    """Submit an approval request."""
    terminal_id = _terminal_id()
    payload = {
        "terminal_id": terminal_id,
        "supervisor_id": supervisor_id,
        "command_text": command_text,
        "metadata_payload": metadata,
    }
    return _request("POST", "/approvals", payload)
