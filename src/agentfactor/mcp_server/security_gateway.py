"""MCP-facing entry point for the AgentFactor security gateway."""

from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP

from agentfactor.mcp_server._http import request, terminal_id


mcp = FastMCP(
    "agentfactor-security",
    instructions=(
        "All upstream MCP calls must use call_security_mcp_tool. "
        "The gateway records, reminds, or blocks according to local policy and never asks for per-call approval."
    ),
)


@mcp.tool()
async def list_security_mcp_inventory() -> dict[str, Any]:
    """List configured upstream MCP servers, tools, and pinned schema status."""
    identity = terminal_id()
    payload = {
        "principal": {"id": identity, "authn": "agentfactor-terminal"},
        "endpoint_id": "agentfactor-local",
        "agent": {
            "name": os.getenv("AGENTFACTOR_SECURITY_AGENT_KIND", "unknown"),
            "version": os.getenv("AGENTFACTOR_SECURITY_AGENT_VERSION"),
        },
        "integration_mode": "gateway",
        "project_id": os.getenv("AGENTFACTOR_PROJECT_ID", "local"),
        "session_id": identity,
        "cwd": os.getcwd(),
    }
    return await request("POST", "/security/v1alpha1/mcp/inventory", payload)


@mcp.tool()
async def call_security_mcp_tool(server_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one upstream MCP tool through the fail-closed security gateway."""
    identity = terminal_id()
    payload = {
        "principal": {"id": identity, "authn": "agentfactor-terminal"},
        "endpoint_id": "agentfactor-local",
        "agent": {
            "name": os.getenv("AGENTFACTOR_SECURITY_AGENT_KIND", "unknown"),
            "version": os.getenv("AGENTFACTOR_SECURITY_AGENT_VERSION"),
        },
        "integration_mode": "gateway",
        "project_id": os.getenv("AGENTFACTOR_PROJECT_ID", "local"),
        "session_id": identity,
        "cwd": os.getcwd(),
        "arguments": arguments,
    }
    return await request(
        "POST",
        f"/security/v1alpha1/mcp/servers/{server_id}/tools/{tool_name}/call",
        payload,
    )


def run() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
