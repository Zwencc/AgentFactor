"""Provider base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import time
from typing import Optional

from agentfactor.clients.tmux import TmuxClient
from agentfactor.models.enums import TerminalStatus
from agentfactor.utils.wsl import command_exists


class ProviderInitializationError(RuntimeError):
    """Raised when a provider cannot start."""


class BaseProvider(ABC):
    """Abstract interface for CLI providers."""

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str],
        tmux: TmuxClient,
    ) -> None:
        self.terminal_id = terminal_id
        self.session_name = session_name
        self.window_name = window_name
        self.agent_profile = agent_profile
        self.tmux = tmux
        self._status = TerminalStatus.READY

    @property
    def status(self) -> TerminalStatus:
        return self._status

    def initialize(self) -> None:
        """Launch the provider inside the tmux window."""
        command = self.build_startup_command()
        if command:
            self.tmux.send_keys(self.session_name, self.window_name, command)
        self._status = TerminalStatus.READY

    def send_input(self, message: str) -> None:
        """Send keystrokes to the provider process."""
        self._status = TerminalStatus.RUNNING
        self.tmux.send_keys(self.session_name, self.window_name, message)
        self._status = TerminalStatus.READY

    def get_status(self) -> TerminalStatus:
        """Return current status."""
        return self._status

    def extract_last_message_from_history(self, history: str) -> str:
        """Return last non-empty block from tmux history."""
        lines = [line.strip() for line in history.rstrip().splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def cleanup(self) -> None:
        """Terminate the provider gracefully."""
        self.tmux.send_keys(self.session_name, self.window_name, "exit")
        self._status = TerminalStatus.COMPLETED

    def answer_startup_prompt(
        self,
        patterns: list[str],
        response_keys: list[str],
        *,
        timeout: float = 5.0,
        polling_interval: float = 0.25,
    ) -> bool:
        """Answer a known startup prompt if it appears in the pane.

        Some CLIs stop on first use of a workspace/version and wait for a
        simple menu answer. This method intentionally handles only explicit
        known prompts supplied by each provider.
        """
        compiled = [re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in patterns]
        deadline = time.time() + timeout
        answered = False

        while time.time() < deadline:
            history = self.tmux.capture_pane(self.session_name, self.window_name)
            if all(pattern.search(history) for pattern in compiled):
                for key in response_keys:
                    if key == "Enter":
                        self.tmux.send_keys(self.session_name, self.window_name, "", enter=True)
                    else:
                        self.tmux.send_keys(self.session_name, self.window_name, key, enter=False)
                answered = True
                break
            time.sleep(polling_interval)

        return answered

    @abstractmethod
    def build_startup_command(self) -> Optional[str]:
        """Return the shell command used to boot the provider."""

    @staticmethod
    def ensure_binary_exists(binary: str) -> None:
        if not command_exists(binary):
            raise ProviderInitializationError(
                f"Required binary '{binary}' not found on PATH or in WSL. Install it before launching the provider."
            )

    def detect_interactive_prompt(self) -> Optional[str]:  # pragma: no cover - default noop
        """Return a textual prompt requiring operator attention, or None if idle."""
        return None
