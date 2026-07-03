"""Shared enums for AgentFactor models."""

from __future__ import annotations

from enum import Enum


class TerminalStatus(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class InboxStatus(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class WorkItemType(str, Enum):
    FEATURE = "feature"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    TEST = "test"
    DOCUMENTATION = "documentation"
    REVIEW = "review"
    INVESTIGATION = "investigation"


class WorkItemStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    IN_PROGRESS = "in_progress"
    NEEDS_VERIFICATION = "needs_verification"
    DONE = "done"
    CANCELLED = "cancelled"


class EdgeType(str, Enum):
    BLOCKS = "blocks"
    ENABLES = "enables"
    CONFLICTS_WITH = "conflicts_with"
    VALIDATES = "validates"
    COLLABORATES_ON = "collaborates_on"


class ProofType(str, Enum):
    GIT_COMMIT = "git_commit"
    TEST_PASS = "test_pass"
    BUG_NOT_REPRODUCED = "bug_not_reproduced"
    REVIEWER_SIGNOFF = "reviewer_signoff"
    COMPLETION_SIGNAL = "completion_signal"


class SignalType(str, Enum):
    """Signal types extracted from terminal output by the stream processor."""

    # Fast-pass (regex, deterministic, confidence=1.0)
    ERROR_OBSERVED = "ERROR_OBSERVED"
    TEST_RESULT = "TEST_RESULT"
    GIT_ACTION = "GIT_ACTION"
    FILE_WRITE = "FILE_WRITE"
    COMPLETION_SIGNAL = "COMPLETION_SIGNAL"
    BLOCKER_SIGNAL = "BLOCKER_SIGNAL"
    CONTEXT_LOSS_SIGNAL = "CONTEXT_LOSS_SIGNAL"

    # Slow-pass (LLM extraction, Phase 3)
    DECISION = "DECISION"
    PROGRESS = "PROGRESS"
    ARCHITECTURE_NOTE = "ARCHITECTURE_NOTE"
    BUG_FOUND = "BUG_FOUND"
    CONTRADICTION_IN_OUTPUT = "CONTRADICTION_IN_OUTPUT"

    # Infrastructure signals (Phase 5)
    COMPACTION_NEEDED = "COMPACTION_NEEDED"
