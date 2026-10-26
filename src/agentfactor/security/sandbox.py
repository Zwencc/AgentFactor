"""Fail-closed Bubblewrap launcher for public CLI agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from agentfactor import constants
from agentfactor.clients.database import SecuritySandboxSession, session_scope


PUBLIC_SANDBOX_PROFILE = "public-agent-compatibility"
STRICT_SANDBOX_PROFILE = "public-agent-strict"
SANDBOX_PROFILES = {PUBLIC_SANDBOX_PROFILE, STRICT_SANDBOX_PROFILE}
SANDBOX_BACKEND = "bubblewrap"
SUPPORTED_PROVIDERS = {"claude-code", "codex"}
CAPABILITIES = {
    "filesystem.host_outside_workspace": "SANDBOX_ENFORCED",
    "filesystem.inside_workspace": "OBSERVED",
    "process.namespace": "SANDBOX_ENFORCED",
    "mcp": "NOT_ENFORCED",
    "network": "NOT_ENFORCED",
}


class SandboxError(RuntimeError):
    """A public Agent could not be placed in the required sandbox."""


class SandboxService:
    def sessions(self) -> list[dict[str, object]]:
        with session_scope() as db:
            values = db.query(SecuritySandboxSession).order_by(SecuritySandboxSession.created_at.desc()).all()
            return [self._session_dict(value) for value in values]

    def session(self, sandbox_id: str) -> dict[str, object] | None:
        with session_scope() as db:
            value = db.get(SecuritySandboxSession, sandbox_id)
            return self._session_dict(value) if value is not None else None

    @staticmethod
    def mark_terminal_ended(terminal_id: str) -> None:
        with session_scope() as db:
            value = db.query(SecuritySandboxSession).filter(SecuritySandboxSession.terminal_id == terminal_id).first()
            if value is not None and value.status == "active":
                value.status = "completed"
                value.ended_at = datetime.now(timezone.utc)

    @staticmethod
    def _session_dict(value: SecuritySandboxSession) -> dict[str, object]:
        return {
            "id": value.id,
            "terminal_id": value.terminal_id,
            "provider": value.provider,
            "backend": value.backend,
            "profile": value.profile,
            "workspace_digest": value.workspace_digest,
            "status": value.status,
            "capabilities": json.loads(value.capabilities),
            "canary_status": value.canary_status,
            "canary_checked_at": value.canary_checked_at,
            "canary_evidence": json.loads(value.canary_evidence),
            "last_error": value.last_error,
            "started_at": value.started_at,
            "ended_at": value.ended_at,
            "created_at": value.created_at,
        }


def public_agent_sandbox_command(
    provider: str,
    terminal_id: str,
    command: Sequence[str],
    profile: str | None = None,
) -> list[str]:
    if provider not in SUPPORTED_PROVIDERS:
        raise SandboxError(f"unsupported public Agent sandbox provider: {provider}")
    if not command:
        raise SandboxError("public Agent sandbox command is empty")
    selected_profile = profile or os.getenv("AGENTFACTOR_PUBLIC_SANDBOX_PROFILE", PUBLIC_SANDBOX_PROFILE)
    if selected_profile not in SANDBOX_PROFILES:
        raise SandboxError(f"unsupported public Agent sandbox profile: {selected_profile}")
    return [
        sys.executable,
        "-m",
        "agentfactor.security.sandbox",
        "launch",
        "--provider",
        provider,
        "--terminal-id",
        terminal_id,
        "--profile",
        selected_profile,
        "--",
        *command,
    ]


def sandbox_home_path(terminal_id: str) -> Path:
    return constants.HOME_DIR / "sandboxes" / terminal_id / "home"


def build_bubblewrap_argv(
    provider: str,
    terminal_id: str,
    workspace: Path,
    command: Sequence[str],
    profile: str = PUBLIC_SANDBOX_PROFILE,
) -> list[str]:
    if provider not in SUPPORTED_PROVIDERS:
        raise SandboxError(f"unsupported public Agent sandbox provider: {provider}")
    if profile not in SANDBOX_PROFILES:
        raise SandboxError(f"unsupported public Agent sandbox profile: {profile}")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SandboxError("bubblewrap is required for public Agent sessions")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise SandboxError("public Agent workspace must be a directory")
    control_root = constants.HOME_DIR.resolve(strict=False)
    if workspace == control_root or control_root in workspace.parents or workspace in control_root.parents:
        raise SandboxError("AgentFactor control data cannot be used as a sandbox workspace")

    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    state_home = sandbox_home_path(terminal_id).resolve()
    _prepare_sandbox_home(state_home, real_home, workspace, provider)

    credentials = _credential_mounts(provider, real_home, state_home)
    binaries = _binary_mounts(provider, real_home, state_home)
    runtimes = _runtime_mounts(real_home, state_home, workspace)

    argv = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind", "/", "/",
        "--tmpfs", "/home",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        "--dir", "/run/user",
        "--proc", "/proc",
        "--dev", "/dev",
        "--dir", str(real_home),
        "--bind", str(state_home), str(real_home),
        "--bind", str(workspace), str(workspace),
        "--setenv", "HOME", str(real_home),
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "XDG_STATE_HOME", str(real_home / ".local" / "state"),
        "--setenv", "XDG_CACHE_HOME", str(real_home / ".cache"),
        "--setenv", "XDG_RUNTIME_DIR", "/run/user",
        "--setenv", "AGENTFACTOR_SANDBOX_ID", f"sandbox_{terminal_id}",
        "--setenv", "AGENTFACTOR_SANDBOX_PROFILE", profile,
        "--chdir", str(workspace),
    ]
    for source, target in credentials:
        argv.extend(["--ro-bind", str(source), str(target)])
    for source, target in binaries:
        argv.extend(["--ro-bind", str(source), str(target)])
    for source, target in runtimes:
        argv.extend(["--ro-bind", str(source), str(target)])
    argv.extend(["--", *command])
    return argv


def _prepare_sandbox_home(state_home: Path, real_home: Path, workspace: Path, provider: str) -> None:
    for relative in (".cache", ".local/bin", ".local/state", ".config"):
        (state_home / relative).mkdir(parents=True, exist_ok=True)
    provider_dir = ".claude" if provider == "claude-code" else ".codex"
    (state_home / provider_dir).mkdir(parents=True, exist_ok=True)
    if real_home == workspace or real_home not in workspace.parents:
        return
    relative_workspace = workspace.relative_to(real_home)
    (state_home / relative_workspace).mkdir(parents=True, exist_ok=True)


def _credential_mounts(provider: str, real_home: Path, state_home: Path) -> list[tuple[Path, Path]]:
    if provider == "claude-code":
        candidates = [
            (real_home / ".claude.json", real_home / ".claude.json"),
            (real_home / ".claude" / ".credentials.json", real_home / ".claude" / ".credentials.json"),
            (real_home / ".claude" / "settings.json", real_home / ".claude" / "settings.json"),
        ]
    else:
        candidates = [
            (real_home / ".codex" / "auth.json", real_home / ".codex" / "auth.json"),
            (real_home / ".codex" / "config.toml", real_home / ".codex" / "config.toml"),
        ]
    mounts: list[tuple[Path, Path]] = []
    for source, target in candidates:
        if not source.is_file():
            continue
        relative = target.relative_to(real_home)
        target_parent = state_home / relative.parent
        target_parent.mkdir(parents=True, exist_ok=True)
        target_file = state_home / relative
        target_file.touch(exist_ok=True)
        mounts.append((source, target))
    return mounts


def _binary_mounts(provider: str, real_home: Path, state_home: Path) -> list[tuple[Path, Path]]:
    names = ["claude"] if provider == "claude-code" else ["codex"]
    if shutil.which("acr"):
        names.append("acr")
    mounts: list[tuple[Path, Path]] = []
    for name in names:
        source_name = shutil.which(name)
        if not source_name:
            raise SandboxError(f"required sandbox executable is unavailable: {name}")
        source = Path(source_name).resolve(strict=True)
        target = real_home / ".local" / "bin" / name
        (state_home / ".local" / "bin" / name).touch(exist_ok=True)
        mounts.append((source, target))
        if name == "codex":
            code_mode_host = source.parent / "codex-code-mode-host"
            if code_mode_host.is_file():
                host_target = real_home / ".local" / "bin" / "codex-code-mode-host"
                (state_home / ".local" / "bin" / "codex-code-mode-host").touch(exist_ok=True)
                mounts.append((code_mode_host, host_target))
    return mounts


def _runtime_mounts(real_home: Path, state_home: Path, workspace: Path) -> list[tuple[Path, Path]]:
    repository = Path(__file__).resolve().parents[3]
    sources = [repository / "src", Path(sys.prefix), Path(sys.base_prefix)]
    mounts: list[tuple[Path, Path]] = []
    for source in sources:
        source = source.resolve()
        if not source.is_dir() or source == Path("/") or real_home not in source.parents:
            continue
        if source == workspace or workspace in source.parents:
            continue
        target = source
        relative = target.relative_to(real_home)
        (state_home / relative).mkdir(parents=True, exist_ok=True)
        if (source, target) not in mounts:
            mounts.append((source, target))
    return mounts


def _security_gateway_config(provider: str, workspace: Path) -> dict[str, dict[str, object]]:
    repository = Path(__file__).resolve().parents[3]
    gateway = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "agentfactor.mcp_server.security_gateway"],
        "env": {
            "PYTHONPATH": str(repository / "src"),
            "AGENTFACTOR_SECURITY_AGENT_KIND": provider,
        },
    }
    servers = {"agentfactor_security": gateway}
    if _acr_hook_ready(provider, workspace):
        servers["acr"] = {
            "type": "stdio",
            "command": "acr",
            "args": ["mcp", "serve", "--root", str(workspace), "--agent", "claude" if provider == "claude-code" else "codex", "--toolset", "directives"],
        }
    return servers


def _acr_hook_ready(provider: str, workspace: Path) -> bool:
    if not (workspace / ".acr" / "project.json").is_file():
        return False
    agent = "claude" if provider == "claude-code" else "codex"
    hook_file = workspace / (".claude/settings.json" if provider == "claude-code" else ".codex/hooks.json")
    try:
        content = hook_file.read_text()
    except OSError:
        return False
    return "acr" in content and "hook" in content and agent in content


def managed_agent_command(
    provider: str,
    workspace: Path,
    command: Sequence[str],
    profile: str = PUBLIC_SANDBOX_PROFILE,
) -> tuple[list[str], dict[str, str]]:
    if profile not in SANDBOX_PROFILES:
        raise SandboxError(f"unsupported public Agent sandbox profile: {profile}")
    if profile == STRICT_SANDBOX_PROFILE and provider == "claude-code":
        raise SandboxError("strict profile rejects Claude Code because built-in MCP routes cannot be removed")
    managed = list(command)
    servers = _security_gateway_config(provider, workspace)
    if provider == "claude-code":
        if "--mcp-config" in managed:
            raise SandboxError("public Claude sessions cannot supply direct MCP configuration")
        managed.extend(["--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": servers}, separators=(",", ":")), "--setting-sources", "user,project,local"])
    else:
        if any("mcp_servers" in part for part in managed):
            raise SandboxError("public Codex sessions cannot override MCP routing")
        try:
            codex_index = managed.index("codex")
        except ValueError as exc:
            raise SandboxError("Codex sandbox command does not contain the codex executable") from exc
        overrides = ["-c", "mcp_servers={}"]
        for name, config in servers.items():
            overrides.extend(["-c", f"mcp_servers.{name}.command={json.dumps(config['command'])}"])
            overrides.extend(["-c", f"mcp_servers.{name}.args={json.dumps(config['args'])}"])
            for key, value in config.get("env", {}).items():
                overrides.extend(["-c", f"mcp_servers.{name}.env.{key}={json.dumps(value)}"])
            overrides.extend(["-c", f"mcp_servers.{name}.required=true"])
        managed[codex_index + 1:codex_index + 1] = overrides

    capabilities = dict(CAPABILITIES)
    if provider == "codex":
        capabilities["mcp"] = "GATEWAY_ENFORCED"
    hook_ready = _acr_hook_ready(provider, workspace)
    capabilities["hook.acr"] = "CONFIGURED" if hook_ready else "NOT_ENFORCED"
    if not hook_ready:
        return managed, capabilities
    agent = "claude" if provider == "claude-code" else "codex"
    if provider == "claude-code":
        wrapped = ["acr", "launch", "--root", str(workspace), "--mode", "native", "--binary", "claude", agent, "--", *managed[1:]]
    else:
        wrapped = ["acr", "launch", "--root", str(workspace), "--mode", "native", "--binary", "/usr/bin/env", agent, "--", *managed[1:]]
    return wrapped, capabilities


def _workspace_digest(workspace: Path) -> str:
    return "sha256:" + hashlib.sha256(str(workspace).encode()).hexdigest()


def _upsert_session(
    terminal_id: str,
    provider: str,
    workspace: Path,
    profile: str,
    status: str,
    error: str | None = None,
    capabilities: dict[str, str] | None = None,
    canary_status: str = "not_run",
    canary_evidence: dict[str, object] | None = None,
) -> str:
    sandbox_id = f"sandbox_{terminal_id}"
    with session_scope() as db:
        value = db.get(SecuritySandboxSession, sandbox_id)
        if value is None:
            value = SecuritySandboxSession(
                id=sandbox_id,
                terminal_id=terminal_id,
                provider=provider,
                backend=SANDBOX_BACKEND,
                profile=profile,
                workspace_digest=_workspace_digest(workspace),
                status=status,
                capabilities=json.dumps(capabilities or CAPABILITIES, separators=(",", ":"), sort_keys=True),
            )
            db.add(value)
        value.status = status
        value.profile = profile
        value.capabilities = json.dumps(capabilities or CAPABILITIES, separators=(",", ":"), sort_keys=True)
        value.canary_status = canary_status
        value.canary_evidence = json.dumps(canary_evidence or {}, separators=(",", ":"), sort_keys=True)
        if canary_status != "not_run":
            value.canary_checked_at = datetime.now(timezone.utc)
        value.last_error = error
        if status == "active":
            value.started_at = datetime.now(timezone.utc)
        if status in {"completed", "failed"}:
            value.ended_at = datetime.now(timezone.utc)
    return sandbox_id


def _run_boundary_canary(
    provider: str,
    terminal_id: str,
    workspace: Path,
    profile: str,
) -> dict[str, object]:
    marker = f".agentfactor-sandbox-canary-{uuid.uuid4().hex}"
    outside = f"/etc/agentfactor-sandbox-canary-{uuid.uuid4().hex}"
    script = (
        "set -eu; "
        f"trap 'rm -f {marker}' EXIT; "
        f": > {marker}; test -f {marker}; "
        "test ! -e \"$HOME/.conductor\"; "
        f"if : > {outside} 2>/dev/null; then rm -f {outside}; exit 23; fi"
    )
    argv = build_bubblewrap_argv(
        provider,
        terminal_id,
        workspace,
        ["/bin/sh", "-c", script],
        profile,
    )
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("sandbox boundary canary could not run") from exc
    if result.returncode != 0:
        raise SandboxError("sandbox boundary canary failed")
    return {
        "workspace_write": "pass",
        "hidden_home": "pass",
        "host_write_block": "pass",
        "network": "not_tested",
    }


def launch(provider: str, terminal_id: str, profile: str, command: Sequence[str]) -> int:
    workspace = Path.cwd().resolve(strict=True)
    capabilities = dict(CAPABILITIES)
    try:
        command, capabilities = managed_agent_command(provider, workspace, command, profile)
        canary_evidence = _run_boundary_canary(provider, terminal_id, workspace, profile)
        argv = build_bubblewrap_argv(provider, terminal_id, workspace, command, profile)
        _upsert_session(
            terminal_id,
            provider,
            workspace,
            profile,
            "active",
            capabilities=capabilities,
            canary_status="pass",
            canary_evidence=canary_evidence,
        )
        process = subprocess.Popen(argv)
        return_code = process.wait()
    except (OSError, SandboxError) as exc:
        _upsert_session(
            terminal_id,
            provider,
            workspace,
            profile,
            "failed",
            "sandbox_launch_failed",
            capabilities,
            canary_status="fail",
        )
        print(f"AgentFactor sandbox failed closed: {exc}", file=sys.stderr)
        return 126
    _upsert_session(
        terminal_id,
        provider,
        workspace,
        profile,
        "completed" if return_code == 0 else "failed",
        None if return_code == 0 else "agent_process_failed",
        capabilities,
        canary_status="pass",
        canary_evidence=canary_evidence,
    )
    return return_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentfactor-sandbox")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    launch_parser = subparsers.add_parser("launch")
    launch_parser.add_argument("--provider", required=True, choices=sorted(SUPPORTED_PROVIDERS))
    launch_parser.add_argument("--terminal-id", required=True)
    launch_parser.add_argument("--profile", required=True, choices=sorted(SANDBOX_PROFILES))
    launch_parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("launch requires a command after --")
    return launch(args.provider, args.terminal_id, args.profile, command)


if __name__ == "__main__":
    raise SystemExit(main())
