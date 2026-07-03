"""FastMCP stdio server entry point for AgentFactor."""

from __future__ import annotations

from fastmcp import FastMCP

from agentfactor.mcp_server.tools import approval, blueprint, identity, orchestrate, work

mcp = FastMCP(
    "agentfactor",
    instructions=(
        "You are connected to AgentFactor. "
        "Use whoami() first to establish your identity, then call the tools relevant to your role. "
        "Workers: use work-item tools to claim and complete tasks. "
        "Conductors: use spawn_worker / assign_task / handoff to delegate. "
        "Planners: use submit_blueprint after generating a TOML work graph."
    ),
)

identity.register(mcp)
blueprint.register(mcp)
work.register(mcp)
orchestrate.register(mcp)
approval.register(mcp)


def run() -> None:
    mcp.run(transport="stdio")
