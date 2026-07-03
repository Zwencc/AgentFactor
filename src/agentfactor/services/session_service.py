"""Session aggregation service."""

from __future__ import annotations

from typing import List

from agentfactor.clients.database import Terminal as TerminalORM, session_scope
from agentfactor.models.session import Session as SessionModel
from agentfactor.services.terminal_service import TerminalService


class SessionService:
    """Provides session-level operations."""

    def __init__(self, terminal_service: TerminalService) -> None:
        self.terminals = terminal_service

    def list_sessions(self) -> List[SessionModel]:
        """Return active sessions and their terminals."""
        with session_scope() as db:
            session_names = [
                row[0]
                for row in db.query(TerminalORM.session_name)
                .distinct()
                .order_by(TerminalORM.session_name)
                .all()
            ]

        return [
            SessionModel(name=name, terminals=self.terminals.list_terminals(name))
            for name in session_names
        ]

    def delete_session(self, session_name: str) -> None:
        """Terminate every terminal in the session."""
        terminals = self.terminals.list_terminals(session_name)
        for terminal in terminals:
            self.terminals.delete_terminal(terminal.id)
