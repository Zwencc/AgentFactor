# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AgentFactor** is a CLI-first orchestrator for coordinating multiple terminal-based AI agents inside tmux sessions. The system manages supervisor and worker agents, handles inter-agent messaging through an inbox system, and provides human-in-the-loop approval workflows for potentially destructive commands.

Core architecture: FastAPI REST server manages tmux sessions, SQLite persistence stores metadata, and CLI tools communicate via HTTP.

> **CLI Alias:** `acd` is a short alias for `agentfactor`. Both commands are interchangeable throughout this documentation.

## Development Commands

### Environment Setup
```bash
acd init      # create ~/.conductor/ directories and initialize SQLite schema
uv sync       # install dependencies
```

### Running the System
```bash
# Start the FastAPI server (required for CLI to function)
uv run uvicorn agentfactor.api.main:app --reload

# Launch a new supervisor session (provider defaults to claude_code)
acd launch --agent-profile conductor
acd launch -p codex --agent-profile conductor  # specify provider

# Spawn workers
acd worker <session-name> --agent-profile tester

# List sessions and inspect
acd sessions          # or: acd ls
acd session <name>    # detailed view of single session

# Quick status and health
acd health            # check if server is running
acd status <id>       # quick terminal status

# Send commands
acd send <id> --message "echo hello"    # or: acd s <id> -m "..."

# Get output
acd output <id> --mode last    # or: acd out <id>

# View logs
acd logs <id>         # last 50 lines
acd logs <id> -f      # follow (tail -f)
acd logs <id> -n 100  # last 100 lines

# Attach to tmux session
acd attach <session-or-id>    # or: acd a <target>

# Kill session or close terminal
acd kill <session> -f    # or: acd k <session> -f
acd close <id>           # or: acd rm <id>

# Approval workflow
acd send <id> --message "rm -rf temp" --require-approval --supervisor <supervisor-id>
acd approvals --status PENDING
acd approve <request-id>
acd deny <request-id> --reason "Too dangerous"

# Persona management
acd persona list      # table view
acd persona show <name>
acd persona edit <name>
acd persona create <name>

# Open the dashboard (optional)
open http://127.0.0.1:9889/admin
```

### Command Aliases

| Full Command | Alias | Description |
|-------------|-------|-------------|
| `sessions` | `ls` | List sessions |
| `output` | `out` | Get terminal output |
| `send` | `s` | Send message to terminal |
| `attach` | `a` | Attach to tmux session |
| `close` | `rm` | Terminate terminal |
| `kill` | `k` | Kill entire session |

### Testing & Quality
```bash
# Run all tests
uv run pytest

# Run a single test (verbose)
uv run pytest tests/test_api.py::test_health_reports_terminal_counts -xvs

# Run with coverage
uv run pytest --cov=agentfactor

# Linting, formatting, type checking
uv run ruff check .
uv run black .          # 100-char line limit
uv run mypy src/agentfactor  # strict mode enabled
```

## Key Architecture Patterns

### Layered Service Architecture
The codebase enforces strict separation of concerns—**never bypass this hierarchy**:

```
CLI (Click) → HTTP → API (FastAPI) → Services → Clients (tmux/DB) → Providers
```

- **CLI** (`agentfactor.cli`) — HTTP client only; never touches tmux or DB directly
- **API** (`agentfactor.api`) — FastAPI routes that delegate to services
- **Services** (`agentfactor.services`) — business logic orchestrating clients/providers
- **Clients** (`agentfactor.clients`) — `tmux.py` wraps libtmux; `database.py` wraps SQLAlchemy
- **Providers** (`agentfactor.providers`) — adapters for specific CLI tools (`claude_code`, `deepseek`, `codex`, `q_cli`)

### Terminal and Session Management
- **Session**: A tmux session (`conductor-<UUID>`) containing one supervisor and zero or more workers
- **Terminal**: A tmux window running a provider, identified by the `CONDUCTOR_TERMINAL_ID` tmux environment variable
- **Provider**: Adapter that launches and manages a specific CLI tool in tmux
- Window names follow the pattern `{role}-{profile}-{provider}` (e.g., `supervisor-conductor-claude_code`)

### Inbox Messaging System
Workers and supervisors communicate asynchronously via an SQLite-backed inbox:
1. Message is queued with `PENDING` status; supports deduplication via `dedupe=True` (used for bootstrapping messages to prevent duplicates on restart)
2. Background loop wakes every 5 seconds, finds receivers with PENDING messages, checks if receiver is idle (READY or COMPLETED status), then injects via `send_input()`
3. Status updates to `DELIVERED` or `FAILED`

### Approval Workflow
Commands requiring approval follow this flow:
1. CLI/MCP call creates `ApprovalRequest` in database
2. Supervisor receives inbox notification
3. Human/agent approves via `acd approve <request-id>`
4. Approved command is sent to terminal; all decisions logged to `~/.conductor/approvals/audit.log`

## Critical Implementation Details

### Provider Interface
All providers must implement `BaseProvider` (`providers/base.py`):
- `build_startup_command() → Optional[str]`: Shell command to launch the CLI tool; return `None` for custom initialization
- `initialize()`: Launch the CLI tool in tmux; raise `ProviderInitializationError` on failure
- `send_input(message)`: Send keystrokes to the process
- `get_status()`: Return terminal state by regex-matching tmux pane history (READY, RUNNING, COMPLETED, ERROR)
- `extract_last_message_from_history(history)`: Parse tmux output for final response
- `detect_interactive_prompt() → Optional[str]`: Detect choice menus awaiting user input (optional; used by prompt loop)
- `cleanup()`: Terminate process and release resources

When adding a new provider:
1. Create subclass in `providers/`
2. Register in `providers/manager.py` registry
3. Implement regex patterns for status detection (READY/RUNNING) and response extraction
4. Optionally implement `detect_interactive_prompt()` for interactive prompt handling

**ClaudeCodeProvider specifics:** Uses `--append-system-prompt` and `--mcp-config` flags; status detection via ⏺ (response), ✶✢✽ (processing), and `>` (idle) markers; tracks `_last_prompt_signature` hash to avoid duplicate prompt detections.

### Database Schema
SQLite at `~/.conductor/db/conductor.db`:
- `terminals`: Terminal metadata (id, tmux session/window, provider, profile, status, timestamps)
- `inbox_messages`: Message queue (sender_id, receiver_id, message, status, created_at)
- `approval_requests`: Approval requests (terminal_id, command_text, supervisor_id, status, decided_at)
- `flows`: Scheduled automation (name, schedule, agent_profile, script, enabled, last_run, next_run)

All DB access uses a context manager (`session_scope()`) with auto-commit/rollback. Always call `db.refresh(obj)` after `db.flush()` to retrieve auto-generated IDs.

### Background Tasks
The API server runs **four** autonomous routines on startup (defined in `api/main.py`):
1. **Cleanup Loop** (every 3600s): Purges COMPLETED terminals and orphan log files based on retention policy
2. **Inbox Loop** (every 5s): Delivers PENDING messages to idle receivers
3. **Prompt Loop** (every 10s): Scans for interactive choice prompts (ClaudeCode only); can integrate with approval workflows
4. **Flow Loop** (every 30s): Evaluates scheduled flows via cron expressions; cron evaluation logic is partially implemented

### Configuration Paths
All runtime data lives under `~/.conductor/` (defined in `constants.py`):
- `agent-context/`: Agent profile markdown files
- `agent-store/`: Bundled example profiles
- `db/`: SQLite database
- `logs/terminal/`: Per-terminal stdout/stderr logs (piped via tmux `pipe-pane`)
- `flows/`: Flow definition files
- `approvals/`: Audit log for approval decisions

## Common Development Patterns

### Adding a New CLI Command
All commands currently live in `cli/main.py` (single file; `cli/commands/` is an empty stub). Steps:
1. Add Click command to `cli/main.py`
2. Define request/response models in `models/` if needed
3. Add corresponding FastAPI route in `api/main.py`
4. Implement business logic in appropriate service module

### Working with tmux
Always use `clients/tmux.py` wrapper, never call `libtmux` directly:
- `create_session()`: Creates session with environment variables
- `create_window()`: Spawns window with predictable naming
- `send_keys()`: Injects commands with proper escaping
- `capture_pane()`: Retrieves terminal history

### MCP Server Integration
Agents access orchestration tools via `mcp_server/server.py`:
- `handoff`: Synchronous delegation (waits for worker completion)
- `assign`: Asynchronous delegation (returns immediately)
- `send_message`: Push message to another terminal's inbox
- `request_approval`: Queue command for supervisor approval

Tools rely on the `CONDUCTOR_TERMINAL_ID` environment variable to identify the caller.

### WSL / Windows Support
`utils/wsl.py` provides path translation between Windows and WSL formats. Use these helpers when constructing paths that cross the WSL boundary, such as log file paths shown to users on Windows.

## Agent Profiles

Agent behavior is defined by markdown files with YAML frontmatter stored in `~/.conductor/agent-context/`. The markdown body becomes the agent's system prompt. Key frontmatter fields:
- `name`: Unique identifier
- `default_provider`: Preferred CLI tool (`claude_code`)
- `mcpServers`: MCP server definitions (injected via `--mcp-config` for ClaudeCode)
- `model`, `allowedTools`, `toolsSettings`, `variables`: Provider-specific settings

See `docs/agent-profile.md` for the full specification.

## Testing Philosophy
Tests are in `tests/` with pytest fixtures in `conftest.py`. Key fixtures:
- `FakeTmuxClient` — in-memory mock that records `send_keys` calls; no real tmux required
- `StubProvider` / `StubProviderManager` — lightweight test doubles
- `temp_runtime_dirs` (autouse) — redirects `~/.conductor/` to `tmp_path`
- `api_client` — `TestClient` with all services mocked

When adding tests: mock tmux via `FakeTmuxClient`, use in-memory SQLite, test service layer independently of API routes.

## Troubleshooting

### "CLI cannot connect to server"
Ensure FastAPI server is running: `uv run uvicorn agentfactor.api.main:app --reload`

### Environment Variable Issues
If agents report missing `CONDUCTOR_TERMINAL_ID`, the agent is running outside Conductor context. Relaunch via `acd launch` and verify tmux environment was set during session creation.

### "tmux session already exists"
Session name collision—delete existing session or choose new name.

### "Provider initialization fails"
Check terminal log at `~/.conductor/logs/terminal/<terminal-id>.log` for missing provider binary, API key errors, or environment variable issues.

### "Inbox messages never arrive"
1. Verify idle prompt regex patterns match provider output
2. Confirm receiver terminal is not continuously streaming
3. Check server logs to confirm background inbox loop is running

## Known Codebase Quirks

1. **Single-file CLI**: `cli/main.py` contains all ~640 lines of CLI commands; `cli/commands/` is an empty stub
2. **Flow scheduling**: Cron evaluation in the flow loop is partially implemented (placeholder logic)
3. **Prompt deduplication**: `ClaudeCodeProvider` caches `_last_prompt_signature` hash to avoid re-detecting the same interactive prompt across loop iterations
4. **libtmux send_keys reliability**: `send_keys(enter=True)` can be flaky; the code sends Enter as an explicit follow-up keystroke for reliability
