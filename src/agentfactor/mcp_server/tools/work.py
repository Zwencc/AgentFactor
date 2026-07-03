"""Work item tools: list/get/claim/complete/block/assign."""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from agentfactor.mcp_server._http import request, terminal_id


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_available_work_items(project_id: str) -> list[dict]:
        """List READY unclaimed work items whose dependencies are done.

        Call this after whoami() to find tasks you can claim.
        """
        return await request("GET", f"/projects/{project_id}/work-items/available")

    @mcp.tool()
    async def get_work_item(item_id: str) -> dict:
        """Fetch full details of a work item including acceptance criteria and proof requirements."""
        return await request("GET", f"/work-items/{item_id}")

    @mcp.tool()
    async def claim_work_item(item_id: str) -> dict:
        """Claim a READY work item. Sets owner to current terminal and status to in_progress.

        Returns error if already claimed by another terminal.
        """
        tid = terminal_id()
        return await request("POST", f"/work-items/{item_id}/claim", {"terminal_id": tid})

    @mcp.tool()
    async def complete_work_item(
        item_id: str,
        summary: str,
        commit_hash: Optional[str] = None,
    ) -> dict:
        """Mark a work item as needs_verification and open its proof window.

        Args:
            item_id: Work item to complete.
            summary: Short description of what was done.
            commit_hash: Optional git commit hash to associate as proof.
        """
        tid = terminal_id()
        return await request("POST", f"/work-items/{item_id}/complete", {
            "terminal_id": tid,
            "summary": summary,
            "commit_hash": commit_hash,
        })

    @mcp.tool()
    async def block_work_item(item_id: str, reason: str) -> dict:
        """Mark a work item as blocked and record why.

        Args:
            item_id: Work item that is stuck.
            reason: Clear description of the blocker.
        """
        tid = terminal_id()
        return await request("POST", f"/work-items/{item_id}/block", {
            "terminal_id": tid,
            "reason": reason,
        })

    @mcp.tool()
    async def list_my_work_items() -> list[dict]:
        """Return all work items currently assigned to this terminal."""
        tid = terminal_id()
        return await request("GET", f"/terminals/{tid}/work-items")

    @mcp.tool()
    async def assign_work_item(item_id: str, target_terminal_id: str) -> dict:
        """Assign a work item directly to a terminal (conductor use — bypasses claim check).

        Args:
            item_id: Work item to assign.
            target_terminal_id: Terminal that should own this item.
        """
        return await request("POST", f"/work-items/{item_id}/assign", {
            "terminal_id": target_terminal_id,
        })
