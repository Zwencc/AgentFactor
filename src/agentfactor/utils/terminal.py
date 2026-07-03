"""Terminal-related helpers."""

from __future__ import annotations

import uuid

from agentfactor import constants


def generate_terminal_id() -> str:
    """Return a random terminal identifier."""
    return uuid.uuid4().hex[:8]


def generate_session_name() -> str:
    """Return a deterministic session name with the configured prefix."""
    return f"{constants.SESSION_PREFIX}{uuid.uuid4().hex[:8]}"


def window_name(role: str, profile_name: str | None, provider: str) -> str:
    """Compose a tmux window name, scoping by provider to keep names unique."""
    suffix = profile_name or "shell"
    return f"{role}-{suffix}-{provider}"
