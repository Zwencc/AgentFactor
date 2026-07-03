"""Approval workflow tools: request_approval, list_pending_approvals, decide_approval."""

from __future__ import annotations

import json
from typing import Optional

from fastmcp import FastMCP

from agentfactor.mcp_server._http import MCPError, request, terminal_id


async def _supervisor_id_for_terminal(tid: str) -> str:
    terminal = await request("GET", f"/terminals/{tid}")
    if str(terminal.get("window_name", "")).startswith("supervisor-"):
        return tid

    session_name = terminal.get("session_name")
    if not session_name:
        raise MCPError("Cannot request approval: current terminal has no session_name.")

    session = await request("GET", f"/sessions/{session_name}")
    for candidate in session.get("terminals", []):
        if str(candidate.get("window_name", "")).startswith("supervisor-"):
            return candidate["id"]

    raise MCPError(
        "Cannot request approval: no supervisor terminal found in current session."
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def request_approval(command_text: str, metadata: Optional[dict] = None) -> dict:
        """Request human approval before executing a potentially destructive command.

        The supervisor terminal receives an inbox notification. Pause execution
        until decide_approval() returns approved=True.

        Args:
            command_text: The command or action requiring approval.
            metadata: Optional structured context (e.g. affected files, risk level).
        """
        tid = terminal_id()
        supervisor_id = await _supervisor_id_for_terminal(tid)
        return await request("POST", "/approvals", {
            "terminal_id": tid,
            "supervisor_id": supervisor_id,
            "command_text": command_text,
            "metadata_payload": json.dumps(metadata) if metadata else None,
        })

    @mcp.tool()
    async def list_pending_approvals() -> list[dict]:
        """List all pending approval requests visible to the current terminal (supervisor role)."""
        tid = terminal_id()
        result = await request("GET", "/approvals?status_filter=PENDING")
        approvals = result if isinstance(result, list) else result.get("items", [])
        return [
            approval
            for approval in approvals
            if approval.get("supervisor_id") == tid
        ]

    @mcp.tool()
    async def decide_approval(request_id: str, approve: bool, reason: Optional[str] = None) -> dict:
        """Approve or deny an approval request.

        Args:
            request_id: ID returned by request_approval().
            approve: True to approve, False to deny.
            reason: Optional explanation (required when denying).
        """
        path = f"/approvals/{request_id}/approve" if approve else f"/approvals/{request_id}/deny"
        payload = None if approve else {"reason": reason}
        return await request("POST", path, payload)
