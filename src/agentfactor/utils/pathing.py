"""Filesystem helpers for AgentFactor."""

from __future__ import annotations

from pathlib import Path

from agentfactor import constants


def ensure_runtime_directories() -> dict[str, Path]:
    """Create the directory tree required for runtime state."""
    required = {
        "home": constants.HOME_DIR,
        "logs": constants.LOG_DIR,
        "terminal_logs": constants.TERMINAL_LOG_DIR,
        "db": constants.DB_DIR,
        "agent_store": constants.AGENT_STORE_DIR,
        "agent_context": constants.AGENT_CONTEXT_DIR,
        "flows": constants.FLOWS_DIR,
        "approvals": constants.APPROVALS_DIR,
    }

    for path in required.values():
        path.mkdir(parents=True, exist_ok=True)

    return required
