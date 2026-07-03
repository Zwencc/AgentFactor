from __future__ import annotations

import json
import os
import shlex
from typing import Any, Dict, List, Optional

import click
import httpx

from agentfactor.clients.database import init_db
from agentfactor.utils import agent_profiles
from agentfactor.utils.logging import setup_logging
from agentfactor.utils.pathing import ensure_runtime_directories
from agentfactor.utils.wsl import to_wsl_path, wsl_available

API_BASE = "http://127.0.0.1:9889"


def _parse_worker_spec(spec: str, default_provider: str, working_directory: str) -> Dict[str, str]:
    if ":" in spec:
        worker_provider, worker_profile = spec.split(":", 1)
        if not worker_provider or not worker_profile:
            raise click.ClickException(
                "--with-worker must be PROFILE or PROVIDER:PROFILE."
            )
    else:
        worker_provider, worker_profile = default_provider, spec
    return {
        "provider": worker_provider,
        "role": "worker",
        "agent_profile": worker_profile,
        "working_directory": working_directory,
    }


def _request(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{API_BASE}{path}"
    with httpx.Client(timeout=60) as client:
        response = client.request(method, url, json=payload)
    if response.status_code >= 400:
        raise click.ClickException(f"API error {response.status_code}: {response.text}")
    if response.content:
        return response.json()
    return None


@click.group(
    help=(
        "AgentFactor CLI for orchestrating tmux-based agents "
        "(providers: claude_code, codex, q_cli, deepseek/deepcode)."
    )
)
def cli() -> None:
    """Root command for AgentFactor."""
    setup_logging()


@cli.command()
def init() -> None:
    """Initialize local directories and database."""
    ensure_runtime_directories()
    init_db()
    click.echo("AgentFactor environment initialized.")


@cli.command()
def health() -> None:
    """Check server health."""
    try:
        result = _request("GET", "/health")
        click.echo(f"Server: {result.get('status', 'unknown')}")
    except Exception as e:
        click.echo(f"Server: offline ({e})")


@cli.command()
@click.argument("source")
@click.option("--name", help="Override the stored filename (defaults to source name).")
@click.option(
    "--scope",
    type=click.Choice(["user", "project"]),
    default="user",
    show_default=True,
    help="Install into the global user store or the project-local .conductor directory.",
)
@click.option("--force/--no-force", default=False, show_default=True, help="Overwrite existing file.")
def install(source: str, name: Optional[str], scope: str, force: bool) -> None:
    """Install an agent persona from bundled profiles or a local file."""
    try:
        result = agent_profiles.install_agent_profile(
            source, name=name, scope=scope, force=force  # type: ignore[arg-type]
        )
    except agent_profiles.AgentProfileError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option(
    "-p",
    "--provider",
    default="claude_code",
    show_default=True,
    help="Provider key (e.g., claude_code, codex, q_cli).",
)
@click.option("--agent-profile", help="Agent profile name used for the terminal.")
@click.option("--role", default="supervisor", show_default=True, help="Role for window naming.")
@click.option(
    "--with-worker",
    "with_workers",
    multiple=True,
    metavar="[PROVIDER:]PROFILE",
    help="Spawn a worker. Use provider:profile to set a different provider per worker.",
)
@click.option(
    "--working-dir",
    "working_directory",
    default=None,
    help="Working directory for the agent (defaults to current directory).",
)
def launch(
    provider: str,
    agent_profile: Optional[str],
    role: str,
    with_workers: List[str],
    working_directory: Optional[str],
) -> None:
    """Launch a new session with a supervisor terminal."""
    cwd = working_directory or os.getcwd()
    payload = {
        "provider": provider,
        "agent_profile": agent_profile,
        "role": role,
        "working_directory": cwd,
    }
    if with_workers:
        payload["workers"] = [
            _parse_worker_spec(spec, provider, cwd)
            for spec in with_workers
        ]
    result = _request("POST", "/sessions", payload)
    session_summary = _request("GET", f"/sessions/{result['session_name']}")
    click.echo(json.dumps(session_summary, indent=2))


@cli.command("sessions")
def list_sessions() -> None:
    """List active sessions."""
    result = _request("GET", "/sessions")
    click.echo(json.dumps(result, indent=2))


@cli.command("session")
@click.argument("session_name")
def get_session(session_name: str) -> None:
    """Get details for a specific session."""
    result = _request("GET", "/sessions")
    for s in result:
        if s["name"] == session_name:
            click.echo(f"Session: {s['name']}")
            terminals = s.get("terminals", [])
            if terminals:
                sup = terminals[0]
                click.echo(f"Supervisor: {sup['id'][:8]} ({sup.get('status', 'unknown')})")
                if len(terminals) > 1:
                    click.echo("Workers:")
                    for t in terminals[1:]:
                        click.echo(f"  - {t['id'][:8]} ({t.get('agent_profile', 'unknown')}, {t.get('status', 'unknown')})")
            return
    raise click.ClickException(f"Session '{session_name}' not found")


@cli.command()
@click.argument("terminal_id")
def status(terminal_id: str) -> None:
    """Get quick status of a terminal."""
    result = _request("GET", f"/terminals/{terminal_id}")
    profile = result.get("agent_profile", "unknown")
    term_status = result.get("status", "unknown")
    click.echo(f"{terminal_id[:8]}: {term_status} ({profile})")


@cli.command()
@click.argument("terminal_id")
@click.option("-n", "--lines", default=50, help="Number of lines to show")
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
def logs(terminal_id: str, lines: int, follow: bool) -> None:
    """View terminal logs."""
    import subprocess
    from pathlib import Path
    log_path = Path.home() / ".conductor" / "logs" / "terminal" / f"{terminal_id}.log"
    if not log_path.exists():
        raise click.ClickException(f"Log file not found: {log_path}")
    log_target = str(log_path)
    tail_command = ["tail"]
    if wsl_available():
        tail_command = ["wsl.exe", "tail"]
        log_target = to_wsl_path(log_path)
    if follow:
        subprocess.run([*tail_command, "-f", log_target])
    else:
        subprocess.run([*tail_command, "-n", str(lines), log_target])


@cli.command()
@click.argument("session_name")
@click.option(
    "-p",
    "--provider",
    default="claude_code",
    show_default=True,
    help="Provider key for the worker (e.g., claude_code, codex, q_cli).",
)
@click.option("--agent-profile", help="Agent profile for the worker.")
@click.option("--role", default="worker", show_default=True, help="Role label.")
@click.option(
    "--working-dir",
    "working_directory",
    default=None,
    help="Working directory for the worker (defaults to current directory).",
)
def worker(
    session_name: str,
    provider: str,
    agent_profile: Optional[str],
    role: str,
    working_directory: Optional[str],
) -> None:
    """Spawn a worker terminal inside an existing session."""
    cwd = working_directory or os.getcwd()
    payload = {
        "provider": provider,
        "agent_profile": agent_profile,
        "role": role,
        "working_directory": cwd,
    }
    result = _request("POST", f"/sessions/{session_name}/terminals", payload)
    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("terminal_id")
@click.option("--message", prompt=True, help="Message to send to the terminal.")
@click.option("--require-approval/--no-require-approval", default=False, show_default=True)
@click.option("--supervisor", help="Supervisor terminal ID when requesting approval.")
@click.option("--metadata", help="Optional metadata payload for approval.")
def send(
    terminal_id: str,
    message: str,
    require_approval: bool,
    supervisor: Optional[str],
    metadata: Optional[str],
) -> None:
    """Send input to a terminal."""
    payload: Dict[str, Any] = {"message": message, "requires_approval": require_approval}
    if require_approval:
        if not supervisor:
            raise click.ClickException("Supervisor terminal ID is required when requesting approval.")
        payload["supervisor_id"] = supervisor
        if metadata is not None:
            payload["metadata_payload"] = metadata
    result = _request("POST", f"/terminals/{terminal_id}/input", payload)
    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("terminal_id")
@click.option("--mode", type=click.Choice(["full", "last"]), default="full", show_default=True)
def output(terminal_id: str, mode: str) -> None:
    """Fetch terminal output."""
    result = _request("GET", f"/terminals/{terminal_id}/output?mode={mode}")
    click.echo(result["output"])


@cli.command()
@click.argument("terminal_id")
def close(terminal_id: str) -> None:
    """Terminate a terminal."""
    _request("DELETE", f"/terminals/{terminal_id}")
    click.echo("Terminal deleted.")


@cli.command()
@click.argument("target")
def attach(target: str) -> None:
    """Attach to a tmux session. TARGET can be session name or terminal ID."""
    import subprocess

    session_name = target

    # If target looks like a short ID (8 chars, hex), try to resolve to session name
    if len(target) <= 8:
        try:
            result = _request("GET", f"/terminals/{target}")
            session_name = result.get("session_name", target)
        except (click.ClickException, httpx.ConnectError, httpx.TimeoutException):
            # API unavailable or terminal not found - use target as-is
            session_name = target

    # Attempt direct tmux attach (works even when API is down)
    command = ["tmux", "attach-session", "-t", session_name]
    if wsl_available():
        command = ["wsl.exe", *command]
    result = subprocess.run(command, capture_output=False)
    if result.returncode != 0:
        raise click.ClickException(
            f"Failed to attach to tmux session '{session_name}'. "
            "Verify the session exists with 'tmux list-sessions'."
        )


@cli.command()
@click.argument("session_name")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def kill(session_name: str, force: bool) -> None:
    """Kill an entire session and all its terminals."""
    if not force:
        click.confirm(f"Kill session '{session_name}' and all terminals?", abort=True)
    _request("DELETE", f"/sessions/{session_name}")
    click.echo(f"Session '{session_name}' killed.")


@cli.command("send-message")
@click.option("--sender", required=True, help="Sender terminal ID.")
@click.option("--receiver", required=True, help="Receiver terminal ID.")
@click.option("--message", prompt=True, help="Message body.")
def send_message(sender: str, receiver: str, message: str) -> None:
    """Queue an inbox message."""
    payload = {"sender_id": sender, "receiver_id": receiver, "message": message}
    result = _request("POST", "/inbox", payload)
    click.echo(json.dumps(result, indent=2))


@cli.command("inbox")
@click.argument("terminal_id")
def inbox(terminal_id: str) -> None:
    """List inbox messages for a terminal."""
    result = _request("GET", f"/inbox/{terminal_id}")
    click.echo(json.dumps(result, indent=2))


@cli.command("approve")
@click.argument("request_id", type=int)
def approve(request_id: int) -> None:
    """Approve a pending command."""
    result = _request("POST", f"/approvals/{request_id}/approve")
    click.echo(json.dumps(result, indent=2))


@cli.command("deny")
@click.argument("request_id", type=int)
@click.option("--reason", help="Reason for denial.")
def deny(request_id: int, reason: Optional[str]) -> None:
    """Deny a pending command."""
    payload = {"reason": reason}
    result = _request("POST", f"/approvals/{request_id}/deny", payload)
    click.echo(json.dumps(result, indent=2))


@cli.command("approvals")
@click.option(
    "--status",
    type=click.Choice(["PENDING", "APPROVED", "DENIED"]),
    help="Optional status filter.",
)
def approvals(status: Optional[str]) -> None:
    """List approval requests."""
    suffix = f"?status_filter={status}" if status else ""
    result = _request("GET", f"/approvals{suffix}")
    click.echo(json.dumps(result, indent=2))


@cli.command("personas")
@click.option("--bundled/--no-bundled", default=True, show_default=True, help="Include bundled personas.")
@click.option(
    "--installed/--no-installed",
    default=True,
    show_default=True,
    help="Include installed personas (user and project scopes).",
)
def personas(bundled: bool, installed: bool) -> None:
    """List available personas from bundled and installed stores."""
    catalog = agent_profiles.get_persona_catalog(
        include_bundled=bundled, include_installed=installed
    )
    click.echo(json.dumps(catalog, indent=2))


@cli.group()
def flow() -> None:
    """Flow management commands."""


@flow.command("register")
@click.option("--name", required=True)
@click.option("--file", "file_path", required=True)
@click.option("--schedule", required=True)
@click.option("--agent-profile", required=True)
@click.option("--script")
def register_flow(name: str, file_path: str, schedule: str, agent_profile: str, script: Optional[str]) -> None:
    payload = {
        "name": name,
        "file_path": file_path,
        "schedule": schedule,
        "agent_profile": agent_profile,
        "script": script,
    }
    result = _request("POST", "/flows", payload)
    click.echo(json.dumps(result, indent=2))


@flow.command("list")
def list_flows() -> None:
    result = _request("GET", "/flows")
    click.echo(json.dumps(result, indent=2))


@flow.command("run")
@click.argument("name")
def run_flow(name: str) -> None:
    result = _request("POST", f"/flows/{name}/run")
    click.echo(json.dumps(result, indent=2))


@flow.command("enable")
@click.argument("name")
def enable_flow(name: str) -> None:
    _request("POST", f"/flows/{name}/enable")
    click.echo("Flow enabled.")


@flow.command("disable")
@click.argument("name")
def disable_flow(name: str) -> None:
    _request("POST", f"/flows/{name}/disable")
    click.echo("Flow disabled.")


@flow.command("remove")
@click.argument("name")
def remove_flow(name: str) -> None:
    _request("DELETE", f"/flows/{name}")
    click.echo("Flow removed.")


# ============================================================================
# COMMAND ALIASES - Short versions of common commands
# ============================================================================


@cli.command("ls")
def ls_alias() -> None:
    """Alias for 'sessions' - list active sessions."""
    list_sessions()


@cli.command("out")
@click.argument("terminal_id")
@click.option("--mode", type=click.Choice(["full", "last"]), default="full", show_default=True)
def out_alias(terminal_id: str, mode: str) -> None:
    """Alias for 'output' - fetch terminal output."""
    output.callback(terminal_id, mode)


@cli.command("s")
@click.argument("terminal_id")
@click.option("--message", "-m", prompt=True, help="Message to send.")
@click.option("--require-approval/--no-require-approval", default=False)
@click.option("--supervisor", help="Supervisor terminal ID.")
@click.option("--metadata", help="Metadata payload.")
def s_alias(terminal_id: str, message: str, require_approval: bool, supervisor: Optional[str], metadata: Optional[str]) -> None:
    """Alias for 'send' - send input to terminal."""
    send.callback(terminal_id, message, require_approval, supervisor, metadata)


@cli.command("a")
@click.argument("target")
def a_alias(target: str) -> None:
    """Alias for 'attach' - attach to tmux session."""
    attach.callback(target)


@cli.command("rm")
@click.argument("terminal_id")
def rm_alias(terminal_id: str) -> None:
    """Alias for 'close' - terminate a terminal."""
    close.callback(terminal_id)


@cli.command("k")
@click.argument("session_name")
@click.option("--force", "-f", is_flag=True)
def k_alias(session_name: str, force: bool) -> None:
    """Alias for 'kill' - kill entire session."""
    kill.callback(session_name, force)


# ============================================================================
# PERSONA MANAGEMENT COMMANDS
# ============================================================================


@cli.group()
def persona() -> None:
    """Persona management commands."""


@persona.command("show")
@click.argument("name")
def persona_show(name: str) -> None:
    """Show the full content of a persona file."""
    from importlib import resources
    from pathlib import Path

    from agentfactor import constants

    # Check project-scoped first (takes precedence), then user-global, then bundled
    project_path = Path.cwd() / ".conductor" / "agent-context" / f"{name}.md"
    user_path = constants.AGENT_CONTEXT_DIR / f"{name}.md"

    if project_path.exists():
        click.echo(project_path.read_text())
    elif user_path.exists():
        click.echo(user_path.read_text())
    else:
        try:
            bundled_file = resources.files("agentfactor.agent_store") / f"{name}.md"
        except FileNotFoundError:
            bundled_file = None
        if bundled_file and bundled_file.is_file():
            click.echo(bundled_file.read_text())
        else:
            raise click.ClickException(f"Persona '{name}' not found")


@persona.command("edit")
@click.argument("name")
def persona_edit(name: str) -> None:
    """Open a persona file in $EDITOR."""
    import subprocess
    from importlib import resources
    from pathlib import Path

    from agentfactor import constants

    # Check project-scoped first (takes precedence), then user-global, then bundled
    project_path = Path.cwd() / ".conductor" / "agent-context" / f"{name}.md"
    user_path = constants.AGENT_CONTEXT_DIR / f"{name}.md"

    if project_path.exists():
        target = project_path
    elif user_path.exists():
        target = user_path
    else:
        try:
            bundled_file = resources.files("agentfactor.agent_store") / f"{name}.md"
        except FileNotFoundError:
            bundled_file = None
        if bundled_file and bundled_file.is_file():
            # Copy bundled to user dir for editing.
            user_path.parent.mkdir(parents=True, exist_ok=True)
            user_path.write_text(bundled_file.read_text())
            target = user_path
            click.echo(f"Copied bundled persona to {user_path} for editing")
        else:
            raise click.ClickException(f"Persona '{name}' not found")

    editor = os.environ.get("EDITOR", "vim")
    subprocess.run([*shlex.split(editor), str(target)])


@persona.command("create")
@click.argument("name")
def persona_create(name: str) -> None:
    """Create a new persona from template."""
    import subprocess

    from agentfactor import constants

    user_path = constants.AGENT_CONTEXT_DIR / f"{name}.md"

    if user_path.exists():
        raise click.ClickException(f"Persona '{name}' already exists at {user_path}")

    user_path.parent.mkdir(parents=True, exist_ok=True)

    template = f"""---
name: {name}
description: Description of {name} agent
default_provider: claude_code
tools: []
mcpServers: {{}}
---

# {name.title()} Agent

System prompt for the {name} agent goes here.

## Responsibilities

- Task 1
- Task 2

## Guidelines

Add any specific guidelines for this agent.
"""
    user_path.write_text(template)
    click.echo(f"Created persona at {user_path}")

    editor = os.environ.get("EDITOR", "vim")
    subprocess.run([*shlex.split(editor), str(user_path)])


@persona.command("list")
@click.option("--bundled/--no-bundled", default=True)
@click.option("--installed/--no-installed", default=True)
def persona_list(bundled: bool, installed: bool) -> None:
    """List available personas in table format."""
    from agentfactor.cli.formatters import table

    catalog = agent_profiles.get_persona_catalog(
        include_bundled=bundled, include_installed=installed
    )

    rows = []
    for source, profiles in catalog.items():
        for p in profiles:
            name = p.get("name", "unknown")
            provider = p.get("default_provider", "claude_code")
            desc = p.get("description", "")[:40]
            rows.append([name, provider, source, desc])

    if rows:
        click.echo(table(["NAME", "PROVIDER", "SOURCE", "DESCRIPTION"], rows))
    else:
        click.echo("No personas found")


if __name__ == "__main__":
    cli()
