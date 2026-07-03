"""Claude Code provider integration."""

from __future__ import annotations

import logging
import re
import shlex
import time
from typing import Optional

from agentfactor.clients.tmux import TmuxError
from agentfactor.models.enums import TerminalStatus
from agentfactor.providers.base import BaseProvider, ProviderInitializationError
from agentfactor.utils.agent_profiles import AgentProfileError, load_agent_profile

ANSI_CODE_PATTERN = r"\x1b\[[0-9;]*m"
RESPONSE_PATTERN = r"⏺(?:\x1b\[[0-9;]*m)*\s+"
PROCESSING_PATTERN = r"[✶✢✽✻·✳].*….*\(esc to interrupt.*\)"
IDLE_PROMPT_PATTERN = r">[\s\xa0]"
WAITING_USER_ANSWER_PATTERN = r"❯.*\d+\."

AUTH_ERROR_PATTERN = r"(API Error:\s*401|Invalid authentication credentials|Please run /login)"

LOG = logging.getLogger(__name__)


class ClaudeCodeProvider(BaseProvider):
    """Provider that manages the Claude Code CLI inside tmux."""

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str],
        tmux,
    ) -> None:
        super().__init__(terminal_id, session_name, window_name, agent_profile, tmux)
        self._initialized = False
        self._last_prompt_signature: Optional[int] = None

    def build_startup_command(self) -> Optional[str]:
        """Not used – command is assembled in initialize."""
        return None

    def _build_claude_command(self) -> list[str]:
        command_parts = ["claude"]

        if self.agent_profile:
            try:
                profile = load_agent_profile(self.agent_profile)
            except AgentProfileError as exc:
                raise ProviderInitializationError(str(exc)) from exc

            prompt_sections = []
            if profile.prompt:
                prompt_sections.append(profile.prompt.strip())
            if profile.body:
                prompt_sections.append(profile.body.strip())
            system_prompt = "\n\n".join(filter(None, prompt_sections)).strip()

            if system_prompt:
                command_parts.extend(["--append-system-prompt", shlex.quote(system_prompt)])

            if profile.mcpServers:
                mcp_json = profile.model_dump_json(include={"mcpServers"})
                command_parts.extend(["--mcp-config", shlex.quote(mcp_json)])

        return command_parts

    def initialize(self) -> None:
        self.ensure_binary_exists("claude")
        command = " ".join(self._build_claude_command())
        self.tmux.send_keys(self.session_name, self.window_name, command)
        self.answer_startup_prompt(
            [
                r"Quick safety check: Is this a project you created or one you trust\?",
                r"Yes, I trust this folder",
                r"No, exit",
            ],
            ["Enter"],
            timeout=8.0,
        )

        if not self._wait_for_status(TerminalStatus.READY, timeout=30.0):
            raise ProviderInitializationError("Claude Code initialization timed out.")

        self._initialized = True
        self._status = TerminalStatus.READY

    def _wait_for_status(
        self, target_status: TerminalStatus, timeout: float, polling_interval: float = 1.0
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.get_status()
            if status == target_status:
                return True
            time.sleep(polling_interval)
        return False

    def get_status(self) -> TerminalStatus:
        output = self.tmux.capture_pane(self.session_name, self.window_name)

        if not output:
            self._status = TerminalStatus.RUNNING
            return self._status

        if re.search(AUTH_ERROR_PATTERN, output, re.IGNORECASE):
            self._status = TerminalStatus.ERROR
            return self._status

        if re.search(PROCESSING_PATTERN, output):
            self._status = TerminalStatus.RUNNING
            return self._status

        if re.search(WAITING_USER_ANSWER_PATTERN, output):
            self._status = TerminalStatus.RUNNING
            return self._status

        if re.search(RESPONSE_PATTERN, output) and re.search(IDLE_PROMPT_PATTERN, output):
            self._status = TerminalStatus.COMPLETED
            return self._status

        if re.search(IDLE_PROMPT_PATTERN, output):
            self._status = TerminalStatus.READY
            return self._status

        self._status = TerminalStatus.RUNNING
        return self._status

    def extract_last_message_from_history(self, history: str) -> str:
        matches = list(re.finditer(RESPONSE_PATTERN, history))
        if not matches:
            raise ValueError("No Claude Code response detected in history.")

        start_pos = matches[-1].end()
        remaining_text = history[start_pos:]

        response_lines = []
        for line in remaining_text.splitlines():
            if re.match(r">\s", line) or "────────" in line:
                break
            stripped = line.strip()
            if stripped:
                response_lines.append(stripped)

        if not response_lines:
            raise ValueError("Claude Code response was empty after ⏺ marker.")

        final_answer = "\n".join(response_lines).strip()
        final_answer = re.sub(ANSI_CODE_PATTERN, "", final_answer)
        return final_answer.strip()

    def cleanup(self) -> None:
        if self._initialized:
            try:
                self.tmux.send_keys(self.session_name, self.window_name, "/exit")
            except TmuxError:
                LOG.warning(
                    "Skipping exit command for %s/%s — tmux window no longer exists.",
                    self.session_name,
                    self.window_name,
                )
        self._status = TerminalStatus.COMPLETED

    def detect_interactive_prompt(self) -> Optional[str]:
        """Return the latest Claude Code choice prompt, if one is awaiting input."""
        history = self.tmux.capture_pane(self.session_name, self.window_name)
        snippet = self._extract_choice_prompt(history)

        if not snippet:
            self._last_prompt_signature = None
            return None

        signature = hash(snippet)
        if signature == self._last_prompt_signature:
            return None

        self._last_prompt_signature = signature
        return snippet

    @staticmethod
    def _extract_choice_prompt(history: str) -> Optional[str]:
        if not history:
            return None

        lines = history.rstrip().splitlines()
        prompt_index: Optional[int] = None
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if re.search(WAITING_USER_ANSWER_PATTERN, line) or line.strip().startswith("❯"):
                prompt_index = idx
                break

        if prompt_index is None:
            return None

        header_lines: list[str] = []
        i = prompt_index - 1
        while i >= 0 and lines[i].strip() and not lines[i].strip().startswith(">"):
            header_lines.insert(0, re.sub(ANSI_CODE_PATTERN, "", lines[i]).strip())
            i -= 1

        option_lines: list[str] = []
        for line in lines[prompt_index:]:
            stripped = re.sub(ANSI_CODE_PATTERN, "", line).rstrip()
            if not stripped:
                break
            if stripped.lstrip().startswith(">"):
                break
            option_lines.append(stripped)

        if not option_lines:
            return None

        body = header_lines + option_lines
        return "\n".join(body).strip()
