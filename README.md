# AgentFactor

AgentFactor is an early vision of the IDE programmers need in the AI era. It is not another general-purpose chat workflow tool, and it is not a loose chain of inefficient, unpredictable sub-agents. Compared with broader automation platforms such as Coze, Lobechat, or Marvis, AgentFactor is focused on professional software development: observable agent behavior, explicit task routing, auditable actions, reproducible logs, and human approval where it matters.

If you are tired of uncontrolled token burn, opaque sub-agent decisions, and agent teams that cannot be predicted or supervised, AgentFactor is designed to bring those workflows back under engineering control. In AgentFactor, development is no longer just about reading code line by line; it becomes the work of decomposing tasks, scheduling agents, validating outputs, reviewing approvals, and tracing every important step.

> Status: AgentFactor is still under active development. APIs, dashboard workflows, and orchestration patterns may continue to change.

AgentFactor is a local agent factory for CLI-based AI workflows. It launches and coordinates multiple terminal agents in tmux, gives them structured messaging and approval gates, and exposes the same runtime through a FastAPI server and web dashboard.

The project is built for engineers who want Claude Code, Codex, DeepSeek, Q CLI, or custom providers to work together inside a local repository without depending on an IDE plugin or hosted orchestration service.

## Disclaimer

AgentFactor is experimental software under active development. Agent orchestration, dashboard flows, APIs, and runtime storage may change before a stable release.

AgentFactor does not guarantee correctness, safety, or completeness of AI-generated output. Always review generated code, commands, dependency changes, file operations, and architectural decisions before applying them to important projects.

AgentFactor is a local orchestration layer, not a security sandbox. Approval gates, logs, and review workflows are designed to improve engineering control, but they do not replace operating-system permissions, backups, secrets management, or human judgment.

References to third-party tools, models, or platforms are for compatibility and positioning only. AgentFactor is not affiliated with, endorsed by, or sponsored by those providers unless explicitly stated.

## Features

- Multi-agent tmux sessions with a supervisor and worker topology.
- Provider adapters for `claude_code`, `codex`, `deepseek`, and `q_cli`.
- Markdown agent profiles with bundled roles such as conductor, developer, tester, reviewer, and document writer.
- CLI commands for launching sessions, sending messages, reading logs, approvals, personas, flows, and work items.
- FastAPI server on `http://127.0.0.1:9889`.
- Fantastic-admin dashboard served at `/admin` when the frontend build exists.
- SQLite persistence for sessions, terminals, inbox messages, approvals, flows, work graphs, reviews, and context history.
- MCP tools for agent self-orchestration, task handoff, approvals, work tracking, and blueprint import.

## Requirements

- Python 3.11+
- tmux 3.x
- uv
- jq for shell examples that parse CLI JSON output
- One or more provider CLIs installed and authenticated, such as Claude Code or Codex

On Windows, the tmux workflow is expected to run through WSL.

## Install

From a local checkout:

```bash
uv sync
uv run agentfactor --help
```

After publishing to GitHub:

```bash
uv tool install --from git+https://github.com/<your-name>/agentfactor.git agentfactor
```

Main command names:

- `agentfactor`
- `af`
- `acd` for compatibility with older scripts

## Quick Start

Initialize runtime directories and the SQLite database:

```bash
agentfactor init
```

Start the local API server:

```bash
uv run python main.py
```

Enable reload during development:

```bash
AGENTFACTOR_RELOAD=1 uv run python main.py
```

Legacy `ACD_HOST`, `ACD_PORT`, and `ACD_RELOAD` environment variables are still supported.

In another terminal, launch a supervisor plus workers:

```bash
RESULT=$(agentfactor launch --provider claude_code --agent-profile conductor \
  --with-worker developer \
  --with-worker tester)

SESSION=$(echo "$RESULT" | jq -r '.name')
SUPERVISOR_ID=$(echo "$RESULT" | jq -r '.terminals[] | select(.window_name | startswith("supervisor-")).id')

agentfactor send "$SUPERVISOR_ID" --message "Implement a small feature and have it tested."
agentfactor attach "$SESSION"
```

Check state:

```bash
agentfactor sessions
agentfactor status "$SUPERVISOR_ID"
agentfactor logs "$SUPERVISOR_ID" -n 100
```

## Common Commands

```bash
agentfactor health
agentfactor launch --agent-profile conductor --with-worker developer
agentfactor worker <session-name> --agent-profile tester
agentfactor send <terminal-id> --message "Your instruction"
agentfactor output <terminal-id> --mode last
agentfactor attach <session-name-or-terminal-id>
agentfactor close <terminal-id>
agentfactor kill <session-name> --force
agentfactor approvals --status PENDING
agentfactor approve <request-id>
agentfactor deny <request-id> --reason "Explain the denial"
agentfactor personas
agentfactor persona list
agentfactor install developer
```

Short aliases include `ls`, `out`, `s`, `a`, `rm`, and `k`.

## Runtime Data

AgentFactor currently keeps the existing conductor runtime layout for compatibility:

- Runtime root: `~/.conductor/`
- Database: `~/.conductor/db/conductor.db`
- Terminal logs: `~/.conductor/logs/terminal/<terminal-id>.log`
- User agent profiles: `~/.conductor/agent-context/`
- Project agent profiles: `.conductor/agent-context/`

Each managed terminal receives `CONDUCTOR_TERMINAL_ID`, which agents and MCP tools use to identify the current terminal.

## API And Dashboard

Start the server with:

```bash
uv run python main.py
```

Useful endpoints:

- `GET /health`
- `GET /sessions`
- `POST /sessions`
- `GET /dashboard/state`
- `GET /providers/health`
- `GET /projects/{project_id}/work-graph`
- `POST /work-graph/generate`

The dashboard is available at:

```text
http://127.0.0.1:9889/admin
```

The frontend source lives in `frontend_fantastic/`.

## MCP Server

AgentFactor includes a FastMCP stdio entry point:

```bash
agentfactor-mcp
```

The legacy `acd-mcp` command is also available.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src/agentfactor
```

Build metadata is in `pyproject.toml`. Runtime dependencies are also listed in `requirements.txt` for environments that do not use uv.

## Project Layout

```text
src/agentfactor/          Python package
src/agentfactor/api/      FastAPI application
src/agentfactor/cli/      Click CLI
src/agentfactor/services/ Business services
src/agentfactor/providers Provider adapters
src/agentfactor/mcp_server FastMCP tools
src/agentfactor/agent_store Bundled agent profiles
frontend_fantastic/       Dashboard frontend
tests/                    Test suite
scripts/                  Local helper scripts
```

## Third-Party Notice

The web dashboard in `frontend_fantastic/` is built on top of Fantastic-admin and keeps Fantastic-admin package names for its internal workspace modules. Fantastic-admin is distributed under the MIT License; see [frontend_fantastic/LICENSE](frontend_fantastic/LICENSE) for the upstream license notice.

## Open Source Statement

AgentFactor is released as open-source software under the GNU General Public License v3.0. See [LICENSE](LICENSE) for the full license text.

You may use, study, modify, and redistribute this project under the terms of GPL-3.0. Contributions are welcome, provided they can be distributed under the same license.
