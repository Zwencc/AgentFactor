"""Thin wrapper around libtmux used by AgentFactor."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import libtmux
from libtmux.session import Session
from libtmux.window import Window

from agentfactor.utils.wsl import ensure_wsl_tmux_wrapper, to_wsl_path, wsl_available


LOG = logging.getLogger(__name__)


class TmuxError(RuntimeError):
    """Raised when tmux interactions fail."""


class TmuxClient:
    """Minimal helper to encapsulate libtmux operations."""

    def __init__(self) -> None:
        self.uses_wsl = wsl_available()
        try:
            if self.uses_wsl:
                self._server = libtmux.Server(tmux_cmd=ensure_wsl_tmux_wrapper())
            else:
                self._server = libtmux.Server()
        except Exception as exc:  # pragma: no cover - libtmux specific
            raise TmuxError("Unable to connect to tmux server.") from exc

    def shell_path(self, path: str) -> str:
        """Return a path as seen by the shell running inside tmux."""
        if self.uses_wsl:
            return to_wsl_path(path)
        return path

    def session_exists(self, name: str) -> bool:
        """Check if a session already exists."""
        return self._server.has_session(name)

    def create_session(
        self,
        session_name: str,
        window_name: str,
        environment: Optional[Dict[str, str]] = None,
        start_directory: Optional[str] = None,
    ) -> Session:
        """Create a new tmux session with an initial window."""
        if self.session_exists(session_name):
            raise TmuxError(f"tmux session '{session_name}' already exists.")

        try:
            kwargs: Dict[str, Any] = {"session_name": session_name, "attach": False}
            if start_directory:
                kwargs["start_directory"] = self.shell_path(start_directory)
            session = self._server.new_session(**kwargs)
        except Exception as exc:  # pragma: no cover - libtmux specific
            raise TmuxError(f"Failed to create tmux session '{session_name}'.") from exc

        window = session.attached_window
        window.rename_window(window_name)
        self._apply_environment(window, environment or {})
        return session

    def create_window(
        self,
        session_name: str,
        window_name: str,
        environment: Optional[Dict[str, str]] = None,
        start_directory: Optional[str] = None,
    ) -> Window:
        """Spawn a new window inside an existing session."""
        session = self._get_session(session_name)
        try:
            kwargs: Dict[str, Any] = {"window_name": window_name, "attach": False}
            if start_directory:
                kwargs["start_directory"] = self.shell_path(start_directory)
            window = session.new_window(**kwargs)
        except Exception as exc:  # pragma: no cover - libtmux specific
            raise TmuxError(
                f"Failed to create window '{window_name}' in session '{session_name}'."
            ) from exc

        self._apply_environment(window, environment or {})
        return window

    def kill_session(self, session_name: str) -> None:
        """Terminate a tmux session."""
        if not self.session_exists(session_name):
            return
        session = self._get_session(session_name)
        session.kill_session()

    def kill_window(self, session_name: str, window_name: str) -> None:
        """Terminate a window inside a session."""
        window = self._get_window(session_name, window_name)
        window.kill_window()

    def send_keys(
        self,
        session_name: str,
        window_name: str,
        keys: str,
        *,
        enter: bool = True,
        suppress_history: bool = False,
        literal: bool = False,
    ) -> None:
        """Send keystrokes to a specific window."""
        window = self._get_window(session_name, window_name)
        pane = window.attached_pane
        pane.send_keys(
            keys,
            enter=False,
            suppress_history=suppress_history,
            literal=literal,
        )
        if enter:
            # libtmux's `enter=True` is occasionally flaky for certain TUIs;
            # send Enter explicitly as a follow-up keystroke for reliability.
            pane.send_keys(
                "Enter",
                enter=False,
                suppress_history=suppress_history,
                literal=False,
            )

    def capture_pane(
        self,
        session_name: str,
        window_name: str,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> str:
        """Return the textual history for a window."""
        window = self._get_window(session_name, window_name)
        pane = window.attached_pane
        result = pane.capture_pane(
            start=start if start is not None else -1000,
            end=end,
        )
        if isinstance(result, list):
            return "\n".join(result)
        return result or ""

    def pipe_pane(
        self,
        session_name: str,
        window_name: str,
        command: str,
        append: bool = True,
    ) -> None:
        """Pipe pane output to an external command (typically tee into a log file)."""
        window = self._get_window(session_name, window_name)
        pane = window.attached_pane
        # Use cmd() to execute pipe-pane command directly
        flag = "-o" if append else ""
        if flag:
            pane.cmd("pipe-pane", flag, command)
        else:
            pane.cmd("pipe-pane", command)

    def _get_session(self, name: str) -> Session:
        session = self._server.find_where({"session_name": name})
        if session is None:
            raise TmuxError(f"tmux session '{name}' not found.")
        return session

    def _get_window(self, session_name: str, window_name: str) -> Window:
        session = self._get_session(session_name)
        window = session.find_where({"window_name": window_name})
        if window is None:
            raise TmuxError(
                f"tmux window '{window_name}' not found in session '{session_name}'."
            )
        return window

    def _apply_environment(self, window: Window, environment: Dict[str, str]) -> None:
        if not environment:
            return
        pane = window.attached_pane
        for key, value in environment.items():
            try:
                pane.cmd("set-environment", key, value)
            except Exception as exc:  # pragma: no cover - libtmux specific
                LOG.warning("Failed to set tmux environment %s: %s", key, exc)
        # pane.cmd("set-environment") only updates the tmux session-level env table;
        # the shell already running in the pane won't see it unless we export explicitly.
        export_expr = " ".join(f"{k}={v}" for k, v in environment.items())
        pane.send_keys(f"export {export_expr}", enter=True)
