"""DeepSeek provider wrapping the deepcode TUI."""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from agentfactor.models.enums import TerminalStatus
from agentfactor.providers.base import BaseProvider, ProviderInitializationError
from agentfactor.utils.agent_profiles import AgentProfileError, load_agent_profile

LOG = logging.getLogger(__name__)

STATUS_PROCESSING_RE = re.compile(r"status:\s*processing", re.IGNORECASE)
STATUS_COMPLETED_RE = re.compile(r"status:\s*completed", re.IGNORECASE)
THINKING_RE = re.compile(r"Thinking\.\.\.", re.IGNORECASE)
READY_INIT_RE = re.compile(r"Type your message", re.IGNORECASE)
RESPONSE_RE = re.compile(r"(?:^|\n)\s*✦\s+(.*?)(?=\nstatus:|\Z)", re.DOTALL)
DEEPCODE_COMMAND = "deepcode"


class DeepSeekProvider(BaseProvider):
    """Provider that manages the DeepSeek TUI CLI inside a tmux pane."""

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str],
        tmux,
    ) -> None:
        super().__init__(terminal_id, session_name, window_name, agent_profile, tmux)
        self._profile = None
        if agent_profile:
            try:
                self._profile = load_agent_profile(agent_profile)
            except AgentProfileError as exc:
                LOG.warning(
                    "Unable to load agent profile %s for DeepSeek provider: %s",
                    agent_profile,
                    exc,
                )

    def build_startup_command(self) -> Optional[str]:
        return None

    def initialize(self) -> None:
        self.tmux.send_keys(self.session_name, self.window_name, DEEPCODE_COMMAND, enter=True)
        self.answer_startup_prompt(
            [
                r"Deep Code latest version has been released",
                r"Ignore this version",
            ],
            ["Down", "Down", "Enter"],
            timeout=8.0,
        )

        if not self._wait_for_ready(timeout=30.0):
            raise ProviderInitializationError(
                f"{DEEPCODE_COMMAND} did not become ready within 30 seconds."
            )

        system_prompt = self._build_system_prompt()
        if system_prompt:
            self._send_text(system_prompt)
            if not self._wait_for_ready(timeout=120.0):
                LOG.warning("%s did not finish processing system prompt in time.", DEEPCODE_COMMAND)

        self._status = TerminalStatus.READY

    def _build_system_prompt(self) -> str:
        if not self._profile:
            return ""
        pieces = [p for p in (self._profile.prompt, self._profile.body) if p]
        return "\n\n".join(p.strip() for p in pieces)

    def _wait_for_ready(self, timeout: float, interval: float = 0.5) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.get_status() == TerminalStatus.READY:
                return True
            time.sleep(interval)
        return False

    def _send_text(self, message: str) -> None:
        """Inject text into deepcode, handling newlines via Shift+Enter."""
        lines = message.split("\n")
        for i, line in enumerate(lines):
            if i > 0:
                self.tmux.send_keys(
                    self.session_name, self.window_name, "S-Enter", enter=False
                )
            if line:
                self.tmux.send_keys(
                    self.session_name, self.window_name, line, enter=False, literal=True
                )
        self.tmux.send_keys(self.session_name, self.window_name, "Enter", enter=False)

    def get_status(self) -> TerminalStatus:
        history = self.tmux.capture_pane(self.session_name, self.window_name)

        if STATUS_PROCESSING_RE.search(history) or THINKING_RE.search(history):
            self._status = TerminalStatus.RUNNING
            return self._status

        if STATUS_COMPLETED_RE.search(history) or READY_INIT_RE.search(history):
            self._status = TerminalStatus.READY
            return self._status

        self._status = TerminalStatus.RUNNING
        return self._status

    def send_input(self, message: str) -> None:
        self._status = TerminalStatus.RUNNING
        self._send_text(message)

    def extract_last_message_from_history(self, history: str) -> str:
        matches = list(RESPONSE_RE.finditer(history))
        if not matches:
            raise ValueError("No completed deepcode response found in history.")
        raw = matches[-1].group(1).strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return "\n".join(lines)

    def cleanup(self) -> None:
        try:
            self.tmux.send_keys(self.session_name, self.window_name, "C-d", enter=False)
        except Exception:
            pass
        self._status = TerminalStatus.COMPLETED
