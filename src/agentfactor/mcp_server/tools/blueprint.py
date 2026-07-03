"""Blueprint tools: submit_blueprint, get_work_graph."""

from __future__ import annotations

from fastmcp import FastMCP

from agentfactor.mcp_server._http import request, terminal_id


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def submit_blueprint(toml_content: str) -> dict:
        """Submit the generated TOML work graph blueprint.

        Call exactly once after producing the blueprint. The system validates,
        persists, and notifies the UI for human review and import.

        Args:
            toml_content: Complete TOML string conforming to the blueprint schema.

        Returns:
            {"status": "accepted", "job_id": int}
        """
        tid = terminal_id()
        return await request("POST", f"/work-graph/blueprint/{tid}", {"toml_content": toml_content})

    @mcp.tool()
    async def get_work_graph(project_id: str) -> dict:
        """Return the current work graph for a project (items, edges, critical path).

        Useful for planners doing incremental revision.
        """
        return await request("GET", f"/projects/{project_id}/work-graph")
