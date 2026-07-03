"""Detect interactive prompts from workers and auto-approve or escalate them."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING, Optional

from agentfactor.models.session import Session
from agentfactor.models.terminal import Terminal
from agentfactor.providers.manager import UnknownProviderError
from agentfactor.services.inbox_service import InboxService
from agentfactor.services.session_service import SessionService
from agentfactor.services.terminal_service import TerminalService

if TYPE_CHECKING:
    from agentfactor.services.approval_service import ApprovalService

LOG = logging.getLogger(__name__)

_PERMISSION_NO_RE = re.compile(r"\d+\.\s+no\b", re.IGNORECASE)

_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+-[rf]+\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bcurl\b.*\|\s*(?:sh|bash)\b"),
    re.compile(r"\bpip(?:3)?\s+install\b.*--break-system-packages\b"),
    re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
    re.compile(r"\bDELETE\b.*\bFROM\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
]


def _is_destructive(prompt_text: str) -> bool:
    return any(p.search(prompt_text) for p in _DESTRUCTIVE_PATTERNS)


def _is_permission_prompt(prompt_text: str) -> bool:
    """True when option 1 is 'Yes' and a numbered 'No' option exists — Claude Code's permission format.

    Handles both 2-option (1. Yes / 2. No) and 3-option (1. Yes / 2. Yes with more / 3. No) prompts.
    """
    lower = prompt_text.lower()
    return "1. yes" in lower and bool(_PERMISSION_NO_RE.search(prompt_text))


class PromptWatcher:
    """Poll providers for interactive prompts, auto-approve safe ones, escalate destructive ones."""

    def __init__(
        self,
        session_service: SessionService,
        terminal_service: TerminalService,
        inbox_service: InboxService,
        approval_service: Optional["ApprovalService"] = None,
    ) -> None:
        self.sessions = session_service
        self.terminals = terminal_service
        self.inbox = inbox_service
        self.approvals = approval_service
        self._last_notified: dict[str, str] = {}

    def scan(self) -> None:
        """Scan all sessions for any terminal awaiting interactive input."""
        for session in self.sessions.list_sessions():
            supervisor = self._locate_supervisor(session)
            for terminal in session.terminals:
                if supervisor and terminal.id == supervisor.id:
                    continue
                self._handle_prompt(supervisor, terminal)

    @staticmethod
    def _locate_supervisor(session: Session) -> Optional[Terminal]:
        for terminal in session.terminals:
            if terminal.window_name.startswith("supervisor-"):
                return terminal
        return None

    def _handle_prompt(self, supervisor: Optional[Terminal], worker: Terminal) -> None:
        try:
            provider = self.terminals.ensure_provider_loaded(worker.id)
        except UnknownProviderError:
            return

        prompt_text = provider.detect_interactive_prompt()
        if not prompt_text:
            self._last_notified.pop(worker.id, None)
            return

        prompt_hash = hashlib.md5(prompt_text.encode("utf-8")).hexdigest()
        if self._last_notified.get(worker.id) == prompt_hash:
            return
        self._last_notified[worker.id] = prompt_hash

        if _is_permission_prompt(prompt_text):
            if _is_destructive(prompt_text):
                self._escalate(supervisor, worker, prompt_text)
            else:
                self._auto_approve(worker, provider)
        else:
            self._notify_supervisor(supervisor, worker, prompt_text)

    def _auto_approve(self, worker: Terminal, provider=None) -> None:
        """Select option 1 (Yes) on a safe non-destructive prompt."""
        try:
            self.terminals.send_input(worker.id, "1")
            LOG.info("Auto-approved prompt for terminal %s", worker.id)
        except Exception:
            LOG.exception("Failed to auto-approve prompt for terminal %s", worker.id)

    def _escalate(self, supervisor: Optional[Terminal], worker: Terminal, prompt_text: str) -> None:
        """Route a destructive prompt to the approval workflow or supervisor inbox."""
        if self.approvals and supervisor:
            try:
                self.approvals.request_approval(
                    terminal_id=worker.id,
                    supervisor_id=supervisor.id,
                    command_text=prompt_text,
                )
                LOG.info(
                    "Created approval request for destructive prompt on terminal %s", worker.id
                )
                return
            except Exception:
                LOG.exception(
                    "Failed to create approval request for terminal %s", worker.id
                )
        self._notify_supervisor(supervisor, worker, prompt_text)

    def _notify_supervisor(
        self, supervisor: Optional[Terminal], worker: Terminal, prompt_text: str
    ) -> None:
        if not supervisor:
            return
        message = (
            f"[PROMPT] {worker.window_name} is awaiting input:\n"
            f"{prompt_text}\n"
            f"Respond via: acd send {worker.id} --message \"<choice>\""
        )
        try:
            self.inbox.queue_message(sender_id=worker.id, receiver_id=supervisor.id, message=message)
        except Exception:
            LOG.exception(
                "Failed to queue prompt notification from %s to %s", worker.id, supervisor.id
            )
