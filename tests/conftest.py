from typing import Dict, Optional

import pytest

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from agentfactor import constants
from agentfactor.api import main as api_main
from agentfactor.clients import database
from agentfactor.models.enums import TerminalStatus
from agentfactor.services.approval_service import ApprovalService
from agentfactor.services.flow_service import FlowService
from agentfactor.services.inbox_service import InboxService
from agentfactor.services.llm_review_service import LLMReviewService
from agentfactor.services.session_service import SessionService
from agentfactor.services.terminal_service import TerminalService
from agentfactor.services.work_service import WorkService
from agentfactor.providers.manager import UnknownProviderError


class FakeTmuxClient:
    """Minimal tmux stand-in for tests."""

    def __init__(self) -> None:
        self.sessions: Dict[str, Dict[str, Dict[str, str]]] = {}
        self.killed_windows = []
        self.killed_sessions = []

    # Session / window lifecycle -------------------------------------------------
    def session_exists(self, name: str) -> bool:
        return name in self.sessions

    def create_session(
        self,
        session_name: str,
        window_name: str,
        environment=None,
        start_directory: Optional[str] = None,
    ):
        if self.session_exists(session_name):
            raise RuntimeError("session exists")
        self.sessions[session_name] = {}
        self.create_window(session_name, window_name, environment)

    def create_window(
        self,
        session_name: str,
        window_name: str,
        environment=None,
        start_directory: Optional[str] = None,
    ):
        session = self.sessions.setdefault(session_name, {})
        session[window_name] = {
            "history": "",
            "environment": environment or {},
            "pipe_commands": [],
        }

    def kill_session(self, session_name: str) -> None:
        self.sessions.pop(session_name, None)
        self.killed_sessions.append(session_name)

    def kill_window(self, session_name: str, window_name: str) -> None:
        windows = self.sessions.get(session_name, {})
        windows.pop(window_name, None)
        self.killed_windows.append((session_name, window_name))

    # Pane interactions ----------------------------------------------------------
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
        self.append_history(session_name, window_name, keys)

    def capture_pane(self, session_name: str, window_name: str, start=None, end=None) -> str:
        return self.sessions[session_name][window_name]["history"]

    def pipe_pane(self, session_name: str, window_name: str, command: str, append: bool = True) -> None:
        self.sessions[session_name][window_name]["pipe_commands"].append((command, append))

    # Test helpers ---------------------------------------------------------------
    def append_history(self, session_name: str, window_name: str, text: str) -> None:
        data = self.sessions[session_name][window_name]
        existing = data["history"]
        data["history"] = f"{existing}{text}\n"


class StubProvider:
    """Lightweight provider for exercising TerminalService."""

    def __init__(self, terminal_id: str, session_name: str, window_name: str, tmux: FakeTmuxClient):
        self.terminal_id = terminal_id
        self.session_name = session_name
        self.window_name = window_name
        self.tmux = tmux
        self.status = TerminalStatus.READY
        self.sent_messages = []
        self.cleaned = False
        self.pending_prompt: Optional[str] = None
        self._prompt_consumed = False

    def initialize(self) -> None:
        self.status = TerminalStatus.READY

    def send_input(self, message: str) -> None:
        self.sent_messages.append(message)
        self.tmux.append_history(self.session_name, self.window_name, message)
        # Simulate immediate completion for tests
        self.status = TerminalStatus.COMPLETED

    def get_status(self) -> TerminalStatus:
        return self.status

    def extract_last_message_from_history(self, history: str) -> str:
        lines = [line for line in history.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def cleanup(self) -> None:
        self.cleaned = True

    def detect_interactive_prompt(self) -> Optional[str]:
        if not self.pending_prompt:
            return None
        if self._prompt_consumed:
            return None
        self._prompt_consumed = True
        return self.pending_prompt


class StubProviderManager:
    """Test double that mirrors ProviderManager behaviour."""

    def __init__(self, tmux: FakeTmuxClient) -> None:
        self.tmux = tmux
        self.providers: Dict[str, StubProvider] = {}

    def create_provider(
        self,
        provider_key: str,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: str | None,
    ) -> StubProvider:
        provider = StubProvider(terminal_id, session_name, window_name, self.tmux)
        provider.initialize()
        self.providers[terminal_id] = provider
        return provider

    def get_provider(self, terminal_id: str) -> StubProvider:
        provider = self.providers.get(terminal_id)
        if not provider:
            raise UnknownProviderError(f"Provider for terminal '{terminal_id}' is not loaded.")
        return provider

    def attach_provider(
        self,
        provider_key: str,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: str | None,
    ) -> StubProvider:
        provider = self.providers.get(terminal_id)
        if provider:
            return provider
        provider = StubProvider(terminal_id, session_name, window_name, self.tmux)
        provider.initialize()
        self.providers[terminal_id] = provider
        return provider

    def cleanup_provider(self, terminal_id: str) -> None:
        provider = self.providers.pop(terminal_id, None)
        if provider:
            provider.cleanup()


@pytest.fixture(autouse=True)
def temp_runtime_dirs(tmp_path, monkeypatch):
    """Redirect runtime directories and database into a temp location."""
    base = tmp_path / "runtime"
    home = base / "home"
    mapping = {
        "HOME_DIR": home,
        "LOG_DIR": home / "logs",
        "TERMINAL_LOG_DIR": home / "logs" / "terminal",
        "DB_DIR": home / "db",
        "DB_FILE": home / "db" / "conductor.db",
        "AGENT_STORE_DIR": home / "agent-store",
        "AGENT_CONTEXT_DIR": home / "agent-context",
        "FLOWS_DIR": home / "flows",
        "APPROVALS_DIR": home / "approvals",
        "BLUEPRINTS_DIR": home / "blueprints",
    }

    for name, path in mapping.items():
        monkeypatch.setattr(constants, name, path)

    database.init_db()
    yield


@pytest.fixture
def fake_tmux():
    return FakeTmuxClient()


@pytest.fixture
def provider_manager(fake_tmux):
    return StubProviderManager(fake_tmux)


@pytest.fixture
def terminal_service(fake_tmux, provider_manager) -> TerminalService:
    return TerminalService(tmux=fake_tmux, providers=provider_manager)


@pytest.fixture
def session_service(terminal_service) -> SessionService:
    return SessionService(terminal_service)


@pytest.fixture
def inbox_service(terminal_service) -> InboxService:
    return InboxService(terminal_service)


@pytest.fixture
def approval_service(terminal_service, inbox_service) -> ApprovalService:
    return ApprovalService(terminal_service, inbox_service)


@pytest.fixture
def flow_service() -> FlowService:
    return FlowService()


@pytest.fixture
def work_service() -> WorkService:
    return WorkService()


@pytest.fixture
def llm_review_service() -> LLMReviewService:
    return LLMReviewService()


@pytest.fixture
def api_client(terminal_service, session_service, inbox_service, approval_service, flow_service, work_service, llm_review_service):
    app = api_main.app

    overrides = {
        api_main.get_terminal_service: lambda: terminal_service,
        api_main.get_session_service: lambda: session_service,
        api_main.get_inbox_service: lambda: inbox_service,
        api_main.get_approval_service: lambda: approval_service,
        api_main.get_flow_service: lambda: flow_service,
        api_main.get_work_service: lambda: work_service,
        api_main.get_llm_review_service: lambda: llm_review_service,
    }

    state_attrs = {
        "terminal_service": terminal_service,
        "session_service": session_service,
        "inbox_service": inbox_service,
        "approval_service": approval_service,
        "flow_service": flow_service,
        "work_service": work_service,
        "llm_review_service": llm_review_service,
    }

    original_state = {name: getattr(app.state, name, None) for name in state_attrs}
    for name, value in state_attrs.items():
        setattr(app.state, name, value)

    original_overrides = app.dependency_overrides.copy()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    app.router.lifespan_context = noop_lifespan
    app.dependency_overrides.update(overrides)

    with TestClient(app) as client:
        yield client

    app.dependency_overrides = original_overrides
    app.router.lifespan_context = original_lifespan

    for name, value in original_state.items():
        if value is None:
            try:
                delattr(app.state, name)
            except AttributeError:
                pass
        else:
            setattr(app.state, name, value)
