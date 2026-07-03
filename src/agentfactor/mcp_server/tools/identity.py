"""Identity tools: whoami, session_status."""

from __future__ import annotations

from fastmcp import FastMCP

from agentfactor.mcp_server._http import MCPError, request, terminal_id


def _role_from_window_name(window_name: str | None) -> str | None:
    if not window_name or "-" not in window_name:
        return None
    return window_name.split("-", 1)[0]


def _identity_payload(terminal: dict, tid: str | None = None) -> dict:
    window_name = terminal.get("window_name")
    return {
        "terminal_id": tid or terminal.get("id"),
        "session_name": terminal.get("session_name"),
        "role": terminal.get("role") or _role_from_window_name(window_name),
        "provider": terminal.get("provider"),
        "agent_profile": terminal.get("agent_profile"),
        "status": terminal.get("status"),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def whoami() -> dict:
        """Return current terminal's identity. Call once at task start to establish context."""
        tid = terminal_id()
        terminal = await request("GET", f"/terminals/{tid}")
        return _identity_payload(terminal, tid)

    @mcp.tool()
    async def session_status() -> list[dict]:
        """Return status snapshot of all terminals in the current session."""
        tid = terminal_id()
        terminal = await request("GET", f"/terminals/{tid}")
        session_name = terminal.get("session_name")
        if not session_name:
            raise MCPError("Could not determine session name from terminal record.")
        session = await request("GET", f"/sessions/{session_name}")
        terminals = session.get("terminals", [])
        return [
            {
                "terminal_id": t.get("id"),
                "window_name": t.get("window_name"),
                "status": t.get("status"),
                "role": t.get("role") or _role_from_window_name(t.get("window_name")),
                "agent_profile": t.get("agent_profile"),
            }
            for t in (terminals if isinstance(terminals, list) else [])
        ]
