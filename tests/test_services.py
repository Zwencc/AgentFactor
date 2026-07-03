import json

from agentfactor import constants
from agentfactor.clients.database import (
    ApprovalRequest as ApprovalORM,
    InboxMessage as InboxORM,
    Terminal as TerminalORM,
    session_scope,
)
from agentfactor.models.enums import ApprovalStatus, InboxStatus, TerminalStatus
from agentfactor.providers.base import BaseProvider
from agentfactor.services.inbox_service import InboxService
from agentfactor.services.prompt_service import PromptWatcher
from agentfactor.services.session_service import SessionService


def test_create_terminal_records_metadata(terminal_service, fake_tmux, provider_manager):
    terminal = terminal_service.create_terminal("claude_code", "supervisor", "conductor")

    assert terminal.provider == "claude_code"
    assert terminal.status == TerminalStatus.READY
    assert terminal.session_name in fake_tmux.sessions
    assert terminal.id in provider_manager.providers

    with session_scope() as db:
        stored = db.get(TerminalORM, terminal.id)
        assert stored is not None
        assert stored.window_name == terminal.window_name
        assert stored.status == TerminalStatus.READY


class StartupPromptProvider(BaseProvider):
    def build_startup_command(self):
        return None


def test_provider_answers_known_startup_prompt(fake_tmux):
    fake_tmux.create_session("prompt-test", "main")
    fake_tmux.append_history(
        "prompt-test",
        "main",
        "Deep Code latest version has been released: 0.1.18 -> 0.1.20\n"
        "1. Install\n2. Ignore once\n3. Ignore this version",
    )
    provider = StartupPromptProvider("term", "prompt-test", "main", None, fake_tmux)

    answered = provider.answer_startup_prompt(
        [r"Deep Code latest version has been released", r"Ignore this version"],
        ["Down", "Down", "Enter"],
        timeout=0.1,
    )

    assert answered is True
    history = fake_tmux.capture_pane("prompt-test", "main")
    assert history.count("Down") == 2


def test_send_input_updates_status_and_history(terminal_service, fake_tmux, provider_manager):
    terminal = terminal_service.create_terminal("claude_code", "worker", "developer")

    terminal_service.send_input(terminal.id, "echo hello")

    provider = provider_manager.providers[terminal.id]
    assert provider.sent_messages == ["echo hello"]

    history = fake_tmux.capture_pane(terminal.session_name, terminal.window_name)
    assert "echo hello" in history

    with session_scope() as db:
        stored = db.get(TerminalORM, terminal.id)
        assert stored.status == TerminalStatus.COMPLETED


def test_send_input_reattaches_provider_when_missing(terminal_service, provider_manager):
    terminal = terminal_service.create_terminal("claude_code", "worker", "developer")
    provider_manager.providers.pop(terminal.id)

    terminal_service.send_input(terminal.id, "echo reattached")

    assert terminal.id in provider_manager.providers
    provider = provider_manager.providers[terminal.id]
    assert provider.sent_messages[-1] == "echo reattached"


def test_capture_output_last_only(terminal_service, fake_tmux):
    terminal = terminal_service.create_terminal("claude_code", "worker", "developer")
    fake_tmux.append_history(terminal.session_name, terminal.window_name, "line-one")
    fake_tmux.append_history(terminal.session_name, terminal.window_name, "line-two")

    full_output = terminal_service.capture_output(terminal.id)
    last_output = terminal_service.capture_output(terminal.id, last_only=True)

    assert "line-one" in full_output and "line-two" in full_output
    assert last_output == "line-two"


def test_delete_terminal_cleans_resources(terminal_service, fake_tmux, provider_manager):
    supervisor = terminal_service.create_terminal("claude_code", "supervisor", "conductor")
    worker = terminal_service.create_terminal(
        "claude_code",
        "worker",
        "developer",
        session_name=supervisor.session_name,
    )

    terminal_service.delete_terminal(worker.id)

    assert worker.id not in provider_manager.providers
    assert (worker.session_name, worker.window_name) in fake_tmux.killed_windows
    assert worker.session_name not in fake_tmux.killed_sessions

    terminal_service.delete_terminal(supervisor.id)

    assert supervisor.session_name in fake_tmux.killed_sessions

    with session_scope() as db:
        assert db.get(TerminalORM, worker.id) is None
        assert db.get(TerminalORM, supervisor.id) is None


def test_session_service_lists_and_deletes(terminal_service):
    supervisor = terminal_service.create_terminal("claude_code", "supervisor", "conductor")
    worker = terminal_service.create_terminal(
        "claude_code",
        "worker",
        "developer",
        session_name=supervisor.session_name,
    )

    session_service = SessionService(terminal_service)
    sessions = session_service.list_sessions()

    assert len(sessions) == 1
    listed = sessions[0]
    assert listed.name == supervisor.session_name
    assert {t.id for t in listed.terminals} == {supervisor.id, worker.id}

    session_service.delete_session(supervisor.session_name)

    with session_scope() as db:
        remaining = db.query(TerminalORM).count()
        assert remaining == 0


def test_inbox_service_delivers_pending(terminal_service, provider_manager):
    inbox_service = InboxService(terminal_service)
    receiver = terminal_service.create_terminal("claude_code", "worker", "tester")

    message = inbox_service.queue_message("sender-id", receiver.id, "Task complete")

    with session_scope() as db:
        stored = db.get(InboxORM, message.id)
        assert stored.status == InboxStatus.PENDING

    inbox_service.deliver_pending(receiver.id)

    provider = provider_manager.providers[receiver.id]
    assert provider.sent_messages[-1] == "[INBOX:sender-id] Task complete"

    with session_scope() as db:
        stored = db.get(InboxORM, message.id)
        assert stored.status == InboxStatus.DELIVERED


def test_inbox_service_waits_until_receiver_idle(terminal_service, provider_manager):
    inbox_service = InboxService(terminal_service)
    receiver = terminal_service.create_terminal("claude_code", "worker", "tester")
    provider = provider_manager.providers[receiver.id]
    provider.status = TerminalStatus.RUNNING

    message = inbox_service.queue_message("sender-id", receiver.id, "Wait for idle")
    inbox_service.deliver_pending(receiver.id)

    assert provider.sent_messages == []
    with session_scope() as db:
        stored = db.get(InboxORM, message.id)
        assert stored.status == InboxStatus.PENDING

    provider.status = TerminalStatus.READY
    inbox_service.deliver_pending(receiver.id)

    assert provider.sent_messages[-1] == "[INBOX:sender-id] Wait for idle"
    with session_scope() as db:
        stored = db.get(InboxORM, message.id)
        assert stored.status == InboxStatus.DELIVERED


def test_prompt_watcher_auto_approves_safe_prompts(
    terminal_service,
    session_service,
    inbox_service,
    provider_manager,
):
    """Claude Code permission prompts (1=Yes, N=No) are auto-approved; non-permission ones go to supervisor inbox."""
    supervisor = terminal_service.create_terminal("claude_code", "supervisor", "conductor")
    worker = terminal_service.create_terminal(
        "claude_code",
        "worker",
        "developer",
        session_name=supervisor.session_name,
    )

    provider = provider_manager.providers[worker.id]
    provider.pending_prompt = "Do you want to proceed?\n❯ 1. Yes\n  2. No"
    provider._prompt_consumed = False

    watcher = PromptWatcher(session_service, terminal_service, inbox_service)
    watcher.scan()

    # Safe binary prompt is auto-approved — "1" sent directly to worker, NOT forwarded to supervisor
    assert "1" in provider.sent_messages
    messages = [msg for msg in inbox_service.list_messages(supervisor.id) if "[PROMPT]" in msg.message]
    assert len(messages) == 0

    # Deduplication: same prompt hash prevents a second auto-approve in the same active window
    provider._prompt_consumed = False
    prev_count = len(provider.sent_messages)
    watcher.scan()
    assert len(provider.sent_messages) == prev_count  # no extra send

    # 3-option prompt (1. Yes / 2. Yes with more permissions / 3. No) is also auto-approved
    provider.pending_prompt = "Do you want to proceed?\n❯ 1. Yes\n  2. Yes, and allow access\n  3. No"
    provider._prompt_consumed = False
    watcher.scan()
    assert provider.sent_messages[-1] == "1"
    assert len([m for m in inbox_service.list_messages(supervisor.id) if "[PROMPT]" in m.message]) == 0

    # Non-binary prompt still notifies supervisor
    provider.pending_prompt = "Select option\n❯ 1. Continue"
    provider._prompt_consumed = False
    watcher.scan()

    supervisor_messages = [msg for msg in inbox_service.list_messages(supervisor.id) if "[PROMPT]" in msg.message]
    assert len(supervisor_messages) == 1
    assert "Select option" in supervisor_messages[0].message


def _read_last_audit_entry():
    log_path = constants.APPROVALS_DIR / "audit.log"
    assert log_path.exists()
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines
    return json.loads(lines[-1])


def test_approval_request_records_and_notifies(
    terminal_service,
    inbox_service,
    approval_service,
):
    supervisor = terminal_service.create_terminal("claude_code", "supervisor", "conductor")
    worker = terminal_service.create_terminal(
        "claude_code",
        "worker",
        "developer",
        session_name=supervisor.session_name,
    )

    approval = approval_service.request_approval(
        terminal_id=worker.id,
        supervisor_id=supervisor.id,
        command_text="rm -rf /tmp",
        metadata_payload="justification",
    )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.metadata_payload == "justification"

    with session_scope() as db:
        stored = db.get(ApprovalORM, approval.id)
        assert stored is not None
        assert stored.command_text == "rm -rf /tmp"

    supervisor_messages = inbox_service.list_messages(supervisor.id)
    assert any("Approval required" in msg.message for msg in supervisor_messages)

    audit_entry = _read_last_audit_entry()
    assert audit_entry["action"] == "REQUESTED"
    assert audit_entry["request_id"] == approval.id


def test_approval_approve_executes_command(
    terminal_service,
    approval_service,
    inbox_service,
    provider_manager,
):
    supervisor = terminal_service.create_terminal("claude_code", "supervisor", "conductor")
    worker = terminal_service.create_terminal(
        "claude_code",
        "worker",
        "developer",
        session_name=supervisor.session_name,
    )

    approval = approval_service.request_approval(worker.id, supervisor.id, "echo approved")

    result = approval_service.approve(approval.id)

    assert result.status == ApprovalStatus.APPROVED

    provider = provider_manager.providers[worker.id]
    assert provider.sent_messages[-1] == "echo approved"

    audit_entry = _read_last_audit_entry()
    assert audit_entry["action"] == "APPROVED"


def test_approval_deny_notifies_worker(
    terminal_service,
    approval_service,
    inbox_service,
):
    supervisor = terminal_service.create_terminal("claude_code", "supervisor", "conductor")
    worker = terminal_service.create_terminal(
        "claude_code",
        "worker",
        "developer",
        session_name=supervisor.session_name,
    )

    approval = approval_service.request_approval(worker.id, supervisor.id, "delete all")

    result = approval_service.deny(approval.id, reason="Too risky")

    assert result.status == ApprovalStatus.DENIED

    worker_messages = inbox_service.list_messages(worker.id)
    assert any("Approval denied" in msg.message for msg in worker_messages)

    audit_entry = _read_last_audit_entry()
    assert audit_entry["action"] == "DENIED"
