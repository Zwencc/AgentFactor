import shutil
import subprocess
from pathlib import Path

import pytest

from agentfactor.clients.database import SecuritySandboxSession, session_scope
from agentfactor.providers.claude_code import ClaudeCodeProvider
from agentfactor.providers.codex import CodexProvider
from agentfactor.security import api as security_api
from agentfactor.security.sandbox import (
    CAPABILITIES,
    PUBLIC_SANDBOX_PROFILE,
    STRICT_SANDBOX_PROFILE,
    SandboxError,
    SandboxService,
    build_bubblewrap_argv,
    managed_agent_command,
    public_agent_sandbox_command,
    _run_boundary_canary,
)


def test_public_agent_commands_are_wrapped_by_fail_closed_launcher(fake_tmux):
    claude = ClaudeCodeProvider("claude-test", "session", "window", None, fake_tmux)
    codex = CodexProvider("codex-test", "session", "window", None, fake_tmux)

    claude_command = claude._build_claude_command()
    codex_command = codex._build_codex_command()

    assert claude_command[-1] == "claude"
    assert claude_command[1:3] == ["-m", "agentfactor.security.sandbox"]
    assert codex_command[1:3] == ["-m", "agentfactor.security.sandbox"]
    assert "codex" in codex_command


def test_sandbox_command_rejects_unknown_provider():
    with pytest.raises(SandboxError, match="unsupported public Agent"):
        public_agent_sandbox_command("unknown", "terminal", ["agent"])


def test_strict_profile_fails_closed_for_claude_builtin_mcp(tmp_path):
    with pytest.raises(SandboxError, match="built-in MCP routes"):
        managed_agent_command(
            "claude-code",
            tmp_path,
            ["claude"],
            STRICT_SANDBOX_PROFILE,
        )


def test_strict_profile_keeps_codex_gateway_enforced(tmp_path):
    command, capabilities = managed_agent_command(
        "codex",
        tmp_path,
        ["codex"],
        STRICT_SANDBOX_PROFILE,
    )

    assert "mcp_servers={}" in command
    assert capabilities["mcp"] == "GATEWAY_ENFORCED"


def test_boundary_canary_requires_all_checks_to_pass(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "agentfactor.security.sandbox.build_bubblewrap_argv",
        lambda *_args: ["bwrap", "canary"],
    )

    class Result:
        returncode = 0

    monkeypatch.setattr("agentfactor.security.sandbox.subprocess.run", lambda *_args, **_kwargs: Result())

    evidence = _run_boundary_canary(
        "codex",
        "terminal",
        workspace,
        PUBLIC_SANDBOX_PROFILE,
    )

    assert evidence == {
        "workspace_write": "pass",
        "hidden_home": "pass",
        "host_write_block": "pass",
        "network": "not_tested",
    }


def test_managed_commands_remove_direct_mcp_routes(tmp_path):
    claude, claude_capabilities = managed_agent_command("claude-code", tmp_path, ["claude"])
    codex, codex_capabilities = managed_agent_command("codex", tmp_path, ["env", "HOME=/home/tester", "codex"])

    assert "--strict-mcp-config" in claude
    assert "agentfactor_security" in " ".join(claude)
    assert "mcp_servers={}" in codex
    assert claude_capabilities["mcp"] == "NOT_ENFORCED"
    assert codex_capabilities["mcp"] == "GATEWAY_ENFORCED"
    assert codex_capabilities["hook.acr"] == "NOT_ENFORCED"


def test_managed_commands_reject_direct_mcp_overrides(tmp_path):
    with pytest.raises(SandboxError, match="direct MCP"):
        managed_agent_command("claude-code", tmp_path, ["claude", "--mcp-config", "{}"])
    with pytest.raises(SandboxError, match="MCP routing"):
        managed_agent_command("codex", tmp_path, ["codex", "-c", "mcp_servers.evil.command=evil"])


def test_registered_acr_workspace_uses_native_launch_and_reports_hook_configuration(tmp_path):
    (tmp_path / ".acr").mkdir()
    (tmp_path / ".acr" / "project.json").write_text("{}")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"command":"acr hook claude pre-tool-use"}')

    command, capabilities = managed_agent_command("claude-code", tmp_path, ["claude", "-p", "test"])

    assert command[:2] == ["acr", "launch"]
    assert "agentfactor_security" in " ".join(command)
    assert '"acr"' in " ".join(command)
    assert capabilities["hook.acr"] == "CONFIGURED"


def test_codex_companion_tool_host_is_mounted(monkeypatch, tmp_path):
    codex = tmp_path / "vendor" / "codex"
    host = codex.parent / "codex-code-mode-host"
    codex.parent.mkdir()
    codex.touch()
    host.touch()
    (tmp_path / "state" / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setattr("agentfactor.security.sandbox.shutil.which", lambda name: str(codex) if name == "codex" else None)

    from agentfactor.security.sandbox import _binary_mounts

    mounts = _binary_mounts("codex", Path("/home/tester"), tmp_path / "state")
    assert (host.resolve(), Path("/home/tester/.local/bin/codex-code-mode-host")) in mounts


def test_runtime_mounts_do_not_make_workspace_subdirectories_read_only(tmp_path):
    from agentfactor.security.sandbox import _runtime_mounts

    repository = Path(__file__).resolve().parents[1]
    mounts = _runtime_mounts(Path.home(), tmp_path / "state", repository)

    assert all(repository not in source.parents and source != repository for source, _target in mounts)


def test_bubblewrap_argv_isolated_home_and_shared_network(monkeypatch, tmp_path):
    monkeypatch.setattr("agentfactor.security.sandbox._credential_mounts", lambda *args: [])
    monkeypatch.setattr("agentfactor.security.sandbox._binary_mounts", lambda *args: [])
    workspace = tmp_path / "project"
    workspace.mkdir()

    argv = build_bubblewrap_argv("codex", "terminal", workspace, ["/bin/true"])

    assert ["--ro-bind", "/", "/"] == argv[argv.index("--ro-bind"):argv.index("--ro-bind") + 3]
    assert ["--tmpfs", "/home"] == argv[argv.index("--tmpfs"):argv.index("--tmpfs") + 2]
    assert ["--bind", str(workspace), str(workspace)] == argv[argv.index("--bind", argv.index("--bind") + 1):argv.index("--bind", argv.index("--bind") + 1) + 3]
    assert "--unshare-net" not in argv


def test_sandbox_rejects_workspace_that_contains_control_data(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("agentfactor.security.sandbox.constants.HOME_DIR", workspace / ".conductor")
    monkeypatch.setattr("agentfactor.security.sandbox.shutil.which", lambda name: "/usr/bin/bwrap")
    with pytest.raises(SandboxError, match="control data"):
        build_bubblewrap_argv("codex", "terminal", workspace, ["/bin/true"])


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
def test_bubblewrap_allows_workspace_write_and_blocks_outside_write(monkeypatch, tmp_path):
    monkeypatch.setattr("agentfactor.security.sandbox._credential_mounts", lambda *args: [])
    monkeypatch.setattr("agentfactor.security.sandbox._binary_mounts", lambda *args: [])
    workspace = tmp_path / "project"
    workspace.mkdir()
    argv = build_bubblewrap_argv(
        "codex",
        "offline-canary",
        workspace,
        ["/bin/sh", "-c", "set -e; test ! -e \"$HOME/.conductor\"; touch inside.txt; touch /etc/agentfactor-sandbox-denied"],
    )

    result = subprocess.run(argv, capture_output=True, text=True)

    assert result.returncode != 0
    assert (workspace / "inside.txt").exists()
    assert "Read-only file system" in result.stderr


def test_sandbox_service_and_api_expose_capability_boundaries(api_client):
    with session_scope() as db:
        db.add(SecuritySandboxSession(
            id="sandbox_terminal-test",
            terminal_id="terminal-test",
            provider="codex",
            backend="bubblewrap",
            profile="public-agent-compatibility",
            workspace_digest="sha256:test",
            status="active",
            capabilities='{"network":"NOT_ENFORCED"}',
        ))

    sessions = SandboxService().sessions()
    assert sessions[0]["capabilities"]["network"] == "NOT_ENFORCED"

    original = api_client.app.dependency_overrides.copy()
    api_client.app.dependency_overrides[security_api.get_sandbox_service] = lambda: SandboxService()
    try:
        response = api_client.get("/security/v1alpha1/sandboxes")
        assert response.status_code == 200
        assert response.json()[0]["id"] == "sandbox_terminal-test"
        assert api_client.get("/security/v1alpha1/sandboxes/missing").status_code == 404
    finally:
        api_client.app.dependency_overrides = original


def test_declared_capabilities_do_not_claim_network_isolation():
    assert CAPABILITIES["network"] == "NOT_ENFORCED"
    assert CAPABILITIES["mcp"] == "NOT_ENFORCED"
    assert CAPABILITIES["filesystem.host_outside_workspace"] == "SANDBOX_ENFORCED"
