"""Terminal stream processor — passive signal extraction from tmux output.

Runs every STREAM_PROCESSOR_INTERVAL_SECS seconds.
For each active terminal:
  1. Captures new lines from tmux pane (diff against previous capture).
  2. Fast-pass: regex patterns emit TerminalSignal events immediately.
  3. Metrics sample: velocity, error density, idle streak every METRICS_SAMPLE_INTERVAL_SECS.

No agent cooperation is required. Agents just work; this watches.
Slow LLM pass (Phase 3): will be added in context_pack_service when context packs need it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from agentfactor.clients.database import EventLog, Snapshot, TerminalMetrics, TerminalORM, session_scope
from agentfactor.models.enums import SignalType, TerminalStatus
from agentfactor.services.event_service import EventService

LOG = logging.getLogger(__name__)

STREAM_PROCESSOR_INTERVAL_SECS = 3
METRICS_SAMPLE_INTERVAL_SECS = 30
EVENT_COMPACTION_THRESHOLD = 200

# ---------------------------------------------------------------------------
# Fast-pass regex patterns
# Each entry: signal_type → list of compiled patterns.
# First match wins per line; one signal type emitted per line.
# ---------------------------------------------------------------------------
_FAST_PATTERNS: list[tuple[SignalType, list[re.Pattern[str]]]] = [
    (
        SignalType.ERROR_OBSERVED,
        [
            re.compile(r"Traceback \(most recent call last\)"),
            re.compile(r"\b(?:Error|Exception|FAILED|AssertionError|SyntaxError|TypeError|RuntimeError)\b"),
            re.compile(r"npm ERR!|yarn error"),
            re.compile(r"ENOENT|EACCES|EPERM"),
        ],
    ),
    (
        SignalType.TEST_RESULT,
        [
            re.compile(r"(\d+) passed(?:,\s*(\d+) failed)?"),
            re.compile(r"(\d+) failed"),
            re.compile(r"ALL TESTS PASSED", re.IGNORECASE),
            re.compile(r"Tests:\s+\d+\s+(?:failed|passed)"),
            re.compile(r"✓ \d+ tests? passing|✗ \d+ tests? failing"),
        ],
    ),
    (
        SignalType.GIT_ACTION,
        [
            re.compile(r"\[[\w/\-]+ [0-9a-f]{7,}\]"),  # [branch abc1234] commit msg
            re.compile(r"^(?:On branch|Changes committed|Committed)\b", re.MULTILINE),
            re.compile(r"\d+ files? changed"),
        ],
    ),
    (
        SignalType.FILE_WRITE,
        [
            re.compile(r"(?:Wrote|Created|Saved|Updated)\s+(?:file\s+)?(\S+\.\w+)"),
            re.compile(r"●\s+(\S+\.\w{1,10})\b"),  # Claude Code file-edit marker
            re.compile(r"^\+\+\+ b/(\S+)", re.MULTILINE),  # diff output
        ],
    ),
    (
        SignalType.COMPLETION_SIGNAL,
        [
            re.compile(r"\b(?:Done|Completed|Finished|All done)\b", re.IGNORECASE),
            re.compile(r"^✅", re.MULTILINE),
            re.compile(r"^Task complete\b", re.MULTILINE | re.IGNORECASE),
        ],
    ),
    (
        SignalType.BLOCKER_SIGNAL,
        [
            re.compile(r"\b(?:blocked|stuck|cannot proceed|waiting for|need clarification)\b", re.IGNORECASE),
            re.compile(r"^(?:⚠️|🚫)", re.MULTILINE),
            re.compile(r"\bpermission denied\b", re.IGNORECASE),
        ],
    ),
    (
        SignalType.CONTEXT_LOSS_SIGNAL,
        [
            re.compile(
                r"\bwhat (?:is|was|are|should be) (?:the|our|my) (?:objective|goal|task|approach|plan)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bwhere (?:should|do) I (?:start|begin|look)\b", re.IGNORECASE),
            re.compile(r"\bcan you remind me\b", re.IGNORECASE),
            re.compile(r"\bI(?:'m| am) not sure (?:what|where|which)\b", re.IGNORECASE),
        ],
    ),
]


@dataclass
class _TerminalBuffer:
    terminal_id: str
    last_content: str = ""
    # Rolling window of (monotonic_time, token_count) for velocity calculation
    token_samples: deque = field(default_factory=lambda: deque(maxlen=200))
    # Timestamps of recent error events for error density
    error_times: deque = field(default_factory=lambda: deque(maxlen=200))
    # Dedup: hashes of recently emitted (signal_type, excerpt[:60]) pairs
    seen_hashes: deque = field(default_factory=lambda: deque(maxlen=300))
    idle_since: Optional[float] = None
    last_metrics_at: float = 0.0


class StreamProcessor:
    """Watches all active terminals and extracts structured signals passively."""

    def __init__(self, terminal_service, event_service: EventService) -> None:
        self._terminals = terminal_service
        self._events = event_service
        self._buffers: dict[str, _TerminalBuffer] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self) -> None:
        import asyncio

        while True:
            try:
                self._tick()
            except Exception:
                LOG.exception("StreamProcessor tick failed")
            await asyncio.sleep(STREAM_PROCESSOR_INTERVAL_SECS)

    def get_latest_metrics(self, terminal_id: str) -> Optional[dict]:
        """Return the most recently sampled metrics for a terminal."""
        from sqlalchemy import desc

        with session_scope() as db:
            row = db.execute(
                select(TerminalMetrics)
                .where(TerminalMetrics.terminal_id == terminal_id)
                .order_by(desc(TerminalMetrics.sampled_at))
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "terminal_id": row.terminal_id,
                "sampled_at": str(row.sampled_at),
                "output_velocity_tpm": row.output_velocity_tpm,
                "error_density": row.error_density,
                "idle_streak_minutes": row.idle_streak_minutes,
                "signal_counts": json.loads(row.signal_counts or "{}"),
            }

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        for terminal_id in self._active_terminal_ids():
            try:
                self._process_terminal(terminal_id, now)
            except Exception:
                LOG.debug("StreamProcessor: error on terminal %s", terminal_id, exc_info=True)

    def _process_terminal(self, terminal_id: str, now: float) -> None:
        try:
            content = self._terminals.capture_output(terminal_id)
        except Exception:
            return

        buf = self._buffers.setdefault(terminal_id, _TerminalBuffer(terminal_id=terminal_id))
        new_lines = _diff_lines(buf.last_content, content)
        buf.last_content = content

        if new_lines:
            buf.idle_since = None
            token_count = sum(len(line.split()) for line in new_lines)
            buf.token_samples.append((now, token_count))
            self._run_fast_pass(terminal_id, new_lines, buf, now)
        else:
            if buf.idle_since is None:
                buf.idle_since = now

        if now - buf.last_metrics_at >= METRICS_SAMPLE_INTERVAL_SECS:
            self._sample_metrics(terminal_id, buf, now)
            buf.last_metrics_at = now

    def _run_fast_pass(
        self,
        terminal_id: str,
        lines: list[str],
        buf: _TerminalBuffer,
        now: float,
    ) -> None:
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            for signal_type, patterns in _FAST_PATTERNS:
                for pattern in patterns:
                    m = pattern.search(stripped)
                    if m:
                        excerpt = stripped[:200]
                        dedup_key = hash((signal_type.value, excerpt[:60]))
                        if dedup_key in buf.seen_hashes:
                            break
                        buf.seen_hashes.append(dedup_key)

                        if signal_type == SignalType.ERROR_OBSERVED:
                            buf.error_times.append(now)

                        self._events.emit(
                            type="TerminalSignal",
                            payload={
                                "signal_type": signal_type.value,
                                "excerpt": excerpt,
                                "groups": [g for g in m.groups() if g is not None],
                                "pass_type": "fast",
                                "confidence": 1.0,
                            },
                            terminal_id=terminal_id,
                        )
                        break  # one signal type per line is enough
                else:
                    continue
                break

    def _sample_metrics(self, terminal_id: str, buf: _TerminalBuffer, now: float) -> None:
        velocity_window = 300.0  # 5-minute rolling window
        recent_tokens = sum(c for t, c in buf.token_samples if now - t <= velocity_window)
        velocity_tpm = (recent_tokens / velocity_window) * 60.0

        error_window = 600.0  # 10-minute rolling window
        recent_errors = sum(1 for t in buf.error_times if now - t <= error_window)
        total_samples = sum(1 for t, _ in buf.token_samples if now - t <= error_window)
        error_density = recent_errors / max(total_samples, 1)

        idle_minutes = ((now - buf.idle_since) / 60.0) if buf.idle_since is not None else 0.0

        with session_scope() as db:
            db.add(
                TerminalMetrics(
                    terminal_id=terminal_id,
                    output_velocity_tpm=round(velocity_tpm, 2),
                    error_density=round(error_density, 4),
                    idle_streak_minutes=round(idle_minutes, 2),
                    signal_counts="{}",
                )
            )

        self._check_compaction_needed(terminal_id)

    def _check_compaction_needed(self, terminal_id: str) -> None:
        """Emit COMPACTION_NEEDED if unsnapshot event volume >= threshold."""
        from sqlalchemy import func as sa_func

        try:
            with session_scope() as db:
                snap = db.execute(
                    select(Snapshot)
                    .where(Snapshot.created_by == terminal_id)
                    .order_by(Snapshot.event_cursor.desc())
                    .limit(1)
                ).scalar_one_or_none()
                last_cursor = snap.event_cursor if snap else 0

                event_count = db.execute(
                    select(sa_func.count(EventLog.id)).where(
                        EventLog.id > last_cursor,
                        EventLog.terminal_id == terminal_id,
                    )
                ).scalar_one()
        except Exception:
            LOG.debug("StreamProcessor: compaction check failed for %s", terminal_id, exc_info=True)
            return

        if event_count >= EVENT_COMPACTION_THRESHOLD:
            self._events.emit(
                type="TerminalSignal",
                payload={
                    "signal_type": SignalType.COMPACTION_NEEDED.value,
                    "event_delta": event_count,
                    "last_cursor": last_cursor,
                    "pass_type": "metrics",
                    "confidence": 1.0,
                },
                terminal_id=terminal_id,
            )

    @staticmethod
    def _active_terminal_ids() -> list[str]:
        with session_scope() as db:
            rows = db.execute(
                select(TerminalORM.id).where(
                    TerminalORM.status.in_([TerminalStatus.READY, TerminalStatus.RUNNING])
                )
            ).all()
            return [row.id for row in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _diff_lines(old: str, new: str) -> list[str]:
    """Return lines present in new but not yet seen in old."""
    if not old:
        return new.splitlines()
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    if len(new_lines) > len(old_lines):
        return new_lines[len(old_lines):]
    # Terminal was cleared or reset — treat all new lines as fresh
    if new != old:
        return new_lines
    return []
