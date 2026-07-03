"""OpenAI Codex provider integration."""

from __future__ import annotations

import logging
import re
import shlex
import time
from pathlib import Path
from typing import Optional

from agentfactor.models.enums import TerminalStatus
from agentfactor.providers.base import BaseProvider, ProviderInitializationError
from agentfactor.utils.agent_profiles import AgentProfileError, load_agent_profile
from agentfactor.utils.wsl import wsl_home

LOG = logging.getLogger(__name__)

READY_PATTERNS = [
    r"100% context left",
    r"Implement \{feature\}",
    r"/model to change",
    r"Type \? for shortcuts",
    r"To get started, describe a task",
]

BUSY_PATTERNS = [
    r"Thinking…",
    r"Running command",
    r"Applying diff",
    r"Executing",
]

PROMPT_RE = re.compile(r"^(?:codex>|›|>>>|\$)\s*$", re.MULTILINE)

HARD_ERROR_PATTERNS = [
    r"failed to initialize rollout recorder",
    r"rollout recorder: (?:operation not permitted|permission denied)",
    r"agent loop died",
    r"fatal error",
    r"panic",
    r"Failed to create session",
]

COMPILED_READY = [re.compile(p, re.IGNORECASE) for p in READY_PATTERNS]
COMPILED_BUSY = [re.compile(p, re.IGNORECASE) for p in BUSY_PATTERNS]
COMPILED_ERRORS = [re.compile(p, re.IGNORECASE) for p in HARD_ERROR_PATTERNS]


class CodexProvider(BaseProvider):
    """Provider that manages the Codex CLI inside tmux."""

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
        self._persona_seeded = False
        if agent_profile:
            try:
                self._profile = load_agent_profile(agent_profile)
            except AgentProfileError as exc:  # pragma: no cover - defensive guard
                LOG.warning("Unable to load agent profile %s for Codex provider: %s", agent_profile, exc)

    def build_startup_command(self) -> Optional[str]:
        # Command is assembled during initialize so we can read profile data.
        return None

    def _profile_var(self, key: str) -> Optional[str]:
        if not self._profile:
            return None
        return (self._profile.variables or {}).get(key)

    def _build_env_prefix(self) -> list[str]:
        """Return ['env', KEY=VAL, ...] so Codex has a writable HOME/TMP and recorder disabled."""
        if getattr(self.tmux, "uses_wsl", False):
            base = f"{wsl_home()}/.conductor/providers/codex/{self.terminal_id}"
        else:
            base_path = Path.home() / ".conductor" / "providers" / "codex" / self.terminal_id
            for sub in ("tmp", "state", "cache", "rollouts"):
                (base_path / sub).mkdir(parents=True, exist_ok=True)
            base = str(base_path)
        defaults = {
            "HOME": self._profile_var("HOME") or (
                f"{wsl_home()}/.conductor" if getattr(self.tmux, "uses_wsl", False) else str(Path.home() / ".conductor")
            ),
            "TMPDIR": self._profile_var("TMPDIR") or f"{base}/tmp",
            "XDG_STATE_HOME": self._profile_var("XDG_STATE_HOME") or f"{base}/state",
            "XDG_CACHE_HOME": self._profile_var("XDG_CACHE_HOME") or f"{base}/cache",
            "CODEX_DISABLE_RECORDER": self._profile_var("CODEX_DISABLE_RECORDER") or "1",
            "CODEX_ROLLOUT_DIR": self._profile_var("CODEX_ROLLOUT_DIR") or f"{base}/rollouts",
        }

        extra_env = self._profile_var("codex_env")
        if extra_env:
            for pair in shlex.split(extra_env):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    defaults[key] = value

        env_prefix = ["env"]
        env_prefix.extend(f"{k}={v}" for k, v in defaults.items())
        return env_prefix

    def _build_codex_command(self) -> list[str]:
        """Assemble the codex command with sensible defaults."""
        cmd = ["codex", "--full-auto", "--sandbox", "workspace-write", "--search"]

        model_from_profile = None
        extra_args = None
        if self._profile:
            model_from_profile = self._profile.model
            extra_args = (self._profile.variables or {}).get("codex_args")

        if model_from_profile:
            cmd.extend(["--model", model_from_profile])
        if extra_args:
            cmd.extend(shlex.split(extra_args))

        return self._build_env_prefix() + cmd

    def initialize(self) -> None:
        self.ensure_binary_exists("codex")
        command = " ".join(shlex.quote(part) for part in self._build_codex_command())
        if getattr(self.tmux, "uses_wsl", False):
            base = f"{wsl_home()}/.conductor/providers/codex/{self.terminal_id}"
            command = f"mkdir -p {shlex.quote(base)}/tmp {shlex.quote(base)}/state {shlex.quote(base)}/cache {shlex.quote(base)}/rollouts && {command}"

        # Nudge shell so the banner renders reliably.
        self.tmux.send_keys(self.session_name, self.window_name, "")
        time.sleep(0.2)

        self.tmux.send_keys(self.session_name, self.window_name, command)
        self.answer_startup_prompt(
            [
                r"Do you trust the contents of this directory\?",
                r"Yes, continue",
                r"No, quit",
            ],
            ["Enter"],
            timeout=8.0,
        )

        if not self._wait_for_status(
            TerminalStatus.READY,
            timeout=60.0,
            polling_interval=0.5,
        ):
            history = self.tmux.capture_pane(self.session_name, self.window_name)
            for cre in COMPILED_ERRORS:
                match = cre.search(history)
                if match:
                    raise ProviderInitializationError(f"Codex init failed: {match.group(0)}")
            raise ProviderInitializationError("Codex initialization timed out.")

        self._status = TerminalStatus.READY
        self._seed_persona()

    def _wait_for_status(
        self,
        target_status: TerminalStatus,
        timeout: float,
        polling_interval: float = 1.0,
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.get_status()
            if status == target_status:
                return True
            time.sleep(polling_interval)
        return False

    def get_status(self) -> TerminalStatus:
        history = self.tmux.capture_pane(self.session_name, self.window_name)
        if not history.strip():
            self._status = TerminalStatus.RUNNING
            return self._status

        for cre in COMPILED_ERRORS:
            if cre.search(history):
                self._status = TerminalStatus.ERROR
                return self._status

        lines = [
            ln.rstrip()
            for ln in history.replace("\r", "").splitlines()
            if ln.strip()
        ]
        if lines and PROMPT_RE.match(lines[-1]):
            self._status = TerminalStatus.READY
            return self._status

        for cre in COMPILED_BUSY:
            if cre.search(history):
                self._status = TerminalStatus.RUNNING
                return self._status

        for cre in COMPILED_READY:
            if cre.search(history):
                self._status = TerminalStatus.READY
                return self._status

        self._status = TerminalStatus.RUNNING
        return self._status

    def extract_last_message_from_history(self, history: str) -> str:
        """Return the last Codex response from the tmux history."""
        sanitized = history.replace("\r", "")
        blocks = [block.strip() for block in re.split(r"\n{2,}", sanitized) if block.strip()]

        for block in reversed(blocks):
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if not lines:
                continue

            text = "\n".join(lines)
            lower_text = text.lower()

            if PROMPT_RE.match(lines[-1]):
                continue
            if any(cre.search(text) for cre in COMPILED_READY):
                continue
            if any(marker in lower_text for marker in ["implement {feature", "100% context left", "to get started", "/model to change"]):
                continue

            return text

        raise ValueError("No Codex response detected in history.")

    def _build_persona_seed_message(self) -> Optional[str]:
        if not self._profile:
            return None

        pieces = []
        if self._profile.prompt:
            pieces.append(self._profile.prompt.strip())
        if self._profile.body:
            pieces.append(self._profile.body.strip())

        extra = self._profile_var("codex_seed_message")
        if extra:
            pieces.append(extra.strip())

        message = "\n\n".join(part for part in pieces if part)
        if not message:
            return None

        acknowledgement = (
            "\n\nPlease acknowledge these instructions and confirm you are ready to coordinate this session."
        )
        return message + acknowledgement

    def _seed_persona(self) -> None:
        if self._persona_seeded:
            return
        seed_message = self._build_persona_seed_message()
        if not seed_message:
            return
        LOG.info(
            "Seeding Codex persona instructions for terminal %s (profile=%s)",
            self.terminal_id,
            self._profile.name,
        )
        started_at = time.time()
        self._status = TerminalStatus.RUNNING
        self.tmux.send_keys(
            self.session_name,
            self.window_name,
            seed_message,
            suppress_history=True,
        )
        try:
            self._wait_for_seed_ack(timeout=15.0)
        except ProviderInitializationError:
            raise
        self._persona_seeded = True
        self._status = TerminalStatus.READY
        LOG.debug(
            "Codex persona seed completed for terminal %s in %.1fs",
            self.terminal_id,
            time.time() - started_at,
        )

    def _wait_for_seed_ack(self, timeout: float) -> None:
        ack_phrase = self._profile_var("codex_seed_ack")
        if ack_phrase:
            ack_phrase = ack_phrase.strip()
        if not ack_phrase:
            if not self._wait_for_status(
                TerminalStatus.READY,
                timeout=timeout,
                polling_interval=0.5,
            ):
                raise ProviderInitializationError(
                    "Codex persona seeding timed out before the idle prompt returned."
                )
            return

        deadline = time.time() + timeout
        while time.time() < deadline:
            history = self.tmux.capture_pane(self.session_name, self.window_name)
            if ack_phrase in history:
                remaining = max(0.5, deadline - time.time())
                if not self._wait_for_status(
                    TerminalStatus.READY,
                    timeout=remaining,
                    polling_interval=0.5,
                ):
                    raise ProviderInitializationError(
                        "Codex acknowledged persona seeding but the prompt did not return."
                    )
                return
            time.sleep(0.5)

        raise ProviderInitializationError(
            "Codex persona seeding timed out waiting for the acknowledgement phrase."
        )
