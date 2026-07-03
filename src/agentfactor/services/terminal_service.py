"""Terminal orchestration service."""

from __future__ import annotations

import logging
import shlex
import textwrap
from typing import List, Optional

from agentfactor import constants
from agentfactor.clients.database import Terminal as TerminalORM, session_scope
from agentfactor.clients.tmux import TmuxClient, TmuxError
from agentfactor.models.enums import TerminalStatus
from agentfactor.models.terminal import Terminal as TerminalModel
from agentfactor.providers.base import BaseProvider, ProviderInitializationError
from agentfactor.providers.manager import ProviderManager, UnknownProviderError
from agentfactor.utils.pathing import ensure_runtime_directories
from agentfactor.utils.terminal import generate_session_name, generate_terminal_id, window_name

LOG = logging.getLogger(__name__)


class TerminalService:
    """Business logic for managing terminals."""

    def __init__(
        self,
        tmux: Optional[TmuxClient] = None,
        providers: Optional[ProviderManager] = None,
        analysis=None,
    ) -> None:
        self.tmux = tmux or TmuxClient()
        self.providers = providers or ProviderManager(self.tmux)
        self._analysis = analysis  # AnalysisService injected lazily to avoid circular imports
        ensure_runtime_directories()

    def _get_analysis_service(self):
        if self._analysis is None:
            from agentfactor.services.analysis_service import AnalysisService
            self._analysis = AnalysisService()
        return self._analysis

    def create_terminal(
        self,
        provider_key: str,
        role: str,
        agent_profile: Optional[str],
        session_name: Optional[str] = None,
        working_directory: Optional[str] = None,
        terminal_id: Optional[str] = None,
    ) -> TerminalModel:
        """Create a terminal, spawning a new tmux session if needed."""
        terminal_id = terminal_id or generate_terminal_id()
        target_session = session_name or generate_session_name()
        window = window_name(role, agent_profile, provider_key)
        environment = {constants.TERMINAL_ENV_VAR: terminal_id}

        if session_name is None:
            self.tmux.create_session(
                target_session, window, environment=environment, start_directory=working_directory
            )
        else:
            self.tmux.create_window(
                target_session, window, environment=environment, start_directory=working_directory
            )

        self._pipe_logs(target_session, window, terminal_id)

        try:
            self.providers.create_provider(
                provider_key=provider_key,
                terminal_id=terminal_id,
                session_name=target_session,
                window_name=window,
                agent_profile=agent_profile,
            )
        except ProviderInitializationError:
            self.tmux.kill_window(target_session, window)
            raise

        db_obj = TerminalORM(
            id=terminal_id,
            session_name=target_session,
            window_name=window,
            provider=provider_key,
            agent_profile=agent_profile,
            status=TerminalStatus.READY,
        )
        with session_scope() as db:
            db.add(db_obj)

        terminal_model = TerminalModel.model_validate(db_obj, from_attributes=True)

        if session_name is not None and not window.startswith("supervisor-"):
            try:
                self._send_worker_bootstrap(
                    session_name=target_session,
                    worker_terminal_id=terminal_id,
                    agent_profile=agent_profile,
                )
            except Exception:  # pragma: no cover - defensive guard
                LOG.warning(
                    "Failed to send bootstrap message to terminal %s",
                    terminal_id,
                    exc_info=True,
                )

        return terminal_model

    def get_terminal(self, terminal_id: str) -> Optional[TerminalModel]:
        """Return terminal metadata if it exists."""
        with session_scope() as db:
            terminal = db.get(TerminalORM, terminal_id)
            if not terminal:
                return None
            return TerminalModel.model_validate(terminal, from_attributes=True)

    def list_terminals(self, session_name: str) -> List[TerminalModel]:
        with session_scope() as db:
            results = (
                db.query(TerminalORM)
                .filter(TerminalORM.session_name == session_name)
                .order_by(TerminalORM.created_at.asc())
                .all()
            )
            return [TerminalModel.model_validate(obj, from_attributes=True) for obj in results]

    def ensure_provider_loaded(self, terminal_id: str) -> BaseProvider:
        """Ensure an in-memory provider exists for a terminal (handles API reloads)."""
        try:
            return self.providers.get_provider(terminal_id)
        except (UnknownProviderError, KeyError):
            terminal = self.get_terminal(terminal_id)
            if not terminal:
                raise
            LOG.info(
                "Re-attaching provider %s for terminal %s (%s/%s)",
                terminal.provider,
                terminal.id,
                terminal.session_name,
                terminal.window_name,
            )
            return self.providers.attach_provider(
                provider_key=terminal.provider,
                terminal_id=terminal.id,
                session_name=terminal.session_name,
                window_name=terminal.window_name,
                agent_profile=terminal.agent_profile,
            )

    def send_input(self, terminal_id: str, message: str) -> None:
        provider = self.ensure_provider_loaded(terminal_id)
        provider.send_input(message)
        self._update_status(terminal_id, provider.get_status())

    def capture_output(self, terminal_id: str, last_only: bool = False) -> str:
        terminal = self.get_terminal(terminal_id)
        if not terminal:
            raise RuntimeError(f"Terminal '{terminal_id}' not found.")
        history = self.tmux.capture_pane(terminal.session_name, terminal.window_name)
        if last_only:
            provider = self.ensure_provider_loaded(terminal_id)
            return provider.extract_last_message_from_history(history)
        return history

    def delete_terminal(self, terminal_id: str) -> None:
        terminal = self.get_terminal(terminal_id)
        if not terminal:
            return

        # Capture analysis before removing the terminal record (log file persists after deletion)
        try:
            self._get_analysis_service().analyze_terminal(
                terminal_id=terminal_id,
                session_name=terminal.session_name,
                window_name=terminal.window_name,
                provider=terminal.provider,
            )
        except Exception:
            LOG.warning("Analysis failed for terminal %s", terminal_id, exc_info=True)

        # Clean up provider and tmux window
        self.providers.cleanup_provider(terminal_id)
        try:
            self.tmux.kill_window(terminal.session_name, terminal.window_name)
        except TmuxError:
            LOG.warning(
                "tmux window %s/%s already missing during delete_terminal(%s)",
                terminal.session_name,
                terminal.window_name,
                terminal_id,
            )

        with session_scope() as db:
            orm_terminal = db.get(TerminalORM, terminal_id)
            if orm_terminal:
                session = orm_terminal.session_name
                db.delete(orm_terminal)
                db.flush()
                remaining = (
                    db.query(TerminalORM)
                    .filter(TerminalORM.session_name == session)
                    .count()
                )
            else:
                session = terminal.session_name
                remaining = 0

        if remaining == 0:
            try:
                self.tmux.kill_session(session)
            except TmuxError:
                LOG.debug(
                    "tmux session %s already missing while cleaning up terminal %s",
                    session,
                    terminal_id,
                )

    def _update_status(self, terminal_id: str, status: TerminalStatus) -> None:
        prev_status = None
        with session_scope() as db:
            terminal = db.get(TerminalORM, terminal_id)
            if not terminal:
                return
            prev_status = terminal.status
            terminal.status = status

        # Auto-analyze when a terminal first reaches COMPLETED (captures final log state)
        if status == TerminalStatus.COMPLETED and prev_status != TerminalStatus.COMPLETED:
            t = self.get_terminal(terminal_id)
            if t:
                try:
                    self._get_analysis_service().analyze_terminal(
                        terminal_id=terminal_id,
                        session_name=t.session_name,
                        window_name=t.window_name,
                        provider=t.provider,
                    )
                except Exception:
                    LOG.debug("Background auto-analysis failed for terminal %s", terminal_id, exc_info=True)
                try:
                    from agentfactor.services.llm_review_service import LLMReviewService
                    LLMReviewService().review_terminal(terminal_id=terminal_id, trigger_source="terminal_completed")
                except Exception:
                    LOG.debug("Background auto-review failed for terminal %s", terminal_id, exc_info=True)

    def _pipe_logs(self, session_name: str, window_name: str, terminal_id: str) -> None:
        log_path = constants.TERMINAL_LOG_DIR / f"{terminal_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        shell_path = self.tmux.shell_path(str(log_path)) if hasattr(self.tmux, "shell_path") else str(log_path)
        command = f"cat >> {shlex.quote(shell_path)}"
        self.tmux.pipe_pane(session_name, window_name, command)

    def _find_supervisor_id(self, session_name: str) -> Optional[str]:
        with session_scope() as db:
            supervisor = (
                db.query(TerminalORM)
                .filter(
                    TerminalORM.session_name == session_name,
                    TerminalORM.window_name.startswith("supervisor-"),
                )
                .order_by(TerminalORM.created_at.asc())
                .first()
            )
            return supervisor.id if supervisor else None

    def _send_worker_bootstrap(
        self,
        *,
        session_name: str,
        worker_terminal_id: str,
        agent_profile: Optional[str],
    ) -> None:
        supervisor_id = self._find_supervisor_id(session_name)
        if not supervisor_id or supervisor_id == worker_terminal_id:
            return

        role_label = agent_profile or "Worker"
        message = textwrap.dedent(
            f"""
            ## COMMUNICATION

            Conductor terminal ID: `{supervisor_id}`

            Send updates:
            `acd s {supervisor_id} -m "{role_label} update: <status>"`

            - Heartbeat: ~1/min during long tasks
            - Blockers: report immediately with context
            - Completion: summarize what was done + next steps

            If you lose the conductor ID:
            `acd ls` and look for window name starting with `supervisor-`.

            ## DEBUGGING YOUR OWN ISSUES

            `acd health`
            `acd status $CONDUCTOR_TERMINAL_ID`
            `acd logs $CONDUCTOR_TERMINAL_ID`
            """
        ).strip()

        self.send_input(worker_terminal_id, message)
