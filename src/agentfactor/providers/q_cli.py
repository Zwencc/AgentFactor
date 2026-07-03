"""Amazon Q CLI provider."""

from __future__ import annotations

from typing import Optional

from agentfactor.providers.base import BaseProvider


class QCLIProvider(BaseProvider):
    """Launches the Amazon Q CLI inside tmux."""

    def build_startup_command(self) -> Optional[str]:
        self.ensure_binary_exists("q")
        if self.agent_profile:
            return f'q chat --agent-profile "{self.agent_profile}"'
        return "q chat"
