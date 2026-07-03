"""Orchestration tools: send_message, spawn_worker, handoff, assign_task, get_terminal_output."""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from agentfactor.mcp_server._http import MCPError, request, terminal_id


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def send_message(receiver_id: str, message: str) -> dict:
        """Send a message to another terminal's inbox.

        Args:
            receiver_id: Target terminal ID.
            message: Message text to deliver.
        """
        sender_id = terminal_id()
        return await request("POST", "/inbox", {
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "message": message,
        })

    @mcp.tool()
    async def spawn_worker(
        agent_profile: str,
        role: str = "worker",
        message: Optional[str] = None,
    ) -> dict:
        """Start a new worker terminal in the current session.

        Args:
            agent_profile: Name of the agent profile to load (e.g. "developer").
            role: Terminal role label, defaults to "worker".
            message: If provided, send this as the initial task via inbox.

        Returns:
            {"terminal_id": str, "session_name": str}
        """
        tid = terminal_id()
        terminal = await request("GET", f"/terminals/{tid}")
        session_name = terminal.get("session_name")
        provider = terminal.get("provider", "claude_code")
        if not session_name:
            raise MCPError("Could not determine session name.")

        worker = await request("POST", f"/sessions/{session_name}/terminals", {
            "provider": provider,
            "agent_profile": agent_profile,
            "role": role,
        })
        worker_id = worker["id"]

        if message:
            await request("POST", "/inbox", {
                "sender_id": tid,
                "receiver_id": worker_id,
                "message": message,
            })

        return {"terminal_id": worker_id, "session_name": session_name}

    @mcp.tool()
    async def assign_task(
        agent_profile: str,
        message: str,
        role: str = "worker",
    ) -> dict:
        """Spawn a worker and send it a task asynchronously (returns immediately).

        Use when you want to delegate and continue without waiting. Poll
        session_status() to track progress.
        """
        tid = terminal_id()
        terminal = await request("GET", f"/terminals/{tid}")
        session_name = terminal.get("session_name")
        provider = terminal.get("provider", "claude_code")
        if not session_name:
            raise MCPError("Could not determine session name.")

        worker = await request("POST", f"/sessions/{session_name}/terminals", {
            "provider": provider,
            "agent_profile": agent_profile,
            "role": role,
        })
        worker_id = worker["id"]
        await request("POST", "/inbox", {
            "sender_id": tid,
            "receiver_id": worker_id,
            "message": message,
        })
        return {"terminal_id": worker_id, "session_name": session_name}

    @mcp.tool()
    async def handoff(
        agent_profile: str,
        message: str,
        role: str = "worker",
    ) -> dict:
        """Spawn a worker, send it a task, and wait for it to complete.

        Suitable for short sub-tasks (under ~2 minutes). Polls the worker until
        COMPLETED or ERROR status is reached.

        Returns:
            {"terminal_id": str, "output": str, "status": str}
        """
        import asyncio

        tid = terminal_id()
        terminal = await request("GET", f"/terminals/{tid}")
        session_name = terminal.get("session_name")
        provider = terminal.get("provider", "claude_code")
        if not session_name:
            raise MCPError("Could not determine session name.")

        worker = await request("POST", f"/sessions/{session_name}/terminals", {
            "provider": provider,
            "agent_profile": agent_profile,
            "role": role,
        })
        worker_id = worker["id"]
        await request("POST", "/inbox", {
            "sender_id": tid,
            "receiver_id": worker_id,
            "message": message,
        })

        for _ in range(24):  # max ~2 min at 5s intervals
            await asyncio.sleep(5)
            try:
                info = await request("GET", f"/terminals/{worker_id}")
            except Exception:
                break
            worker_status = info.get("status", "")
            if worker_status in ("COMPLETED", "ERROR"):
                output = await request("GET", f"/terminals/{worker_id}/output?mode=last")
                return {
                    "terminal_id": worker_id,
                    "output": output.get("output", "") if isinstance(output, dict) else str(output),
                    "status": worker_status,
                }

        return {"terminal_id": worker_id, "output": "", "status": "TIMEOUT"}

    @mcp.tool()
    async def get_terminal_output(terminal_id_target: str, mode: str = "last") -> dict:
        """Get output from a terminal.

        Args:
            terminal_id_target: Terminal to query.
            mode: "last" for the most recent response, "full" for complete pane history.
        """
        result = await request("GET", f"/terminals/{terminal_id_target}/output?mode={mode}")
        return {
            "terminal_id": terminal_id_target,
            "output": result.get("output", "") if isinstance(result, dict) else str(result),
        }
