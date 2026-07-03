"""Local development entry point for AgentFactor."""

from __future__ import annotations

import os

import uvicorn

from agentfactor.constants import SERVER_HOST, SERVER_PORT


def _env(name: str, legacy_name: str, default: str) -> str:
    """Read the AgentFactor env var, falling back to the legacy ACD name."""
    return os.getenv(name) or os.getenv(legacy_name) or default


def main() -> None:
    """Run the FastAPI server with the project's default local settings."""
    host = _env("AGENTFACTOR_HOST", "ACD_HOST", SERVER_HOST)
    port = int(_env("AGENTFACTOR_PORT", "ACD_PORT", str(SERVER_PORT)))
    reload = _env("AGENTFACTOR_RELOAD", "ACD_RELOAD", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    uvicorn.run(
        "agentfactor.api.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
