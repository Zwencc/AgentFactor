"""Context Loss Detector — monitors terminals for context disorientation signals.

Runs every 60 seconds. If a terminal emits >= SIGNAL_THRESHOLD
CONTEXT_LOSS_SIGNAL events within a WINDOW_SECS rolling window,
triggers a recovery protocol:

  1. Build a fresh context pack for the terminal.
  2. Format and deliver it as a priority inbox message.
  3. Emit a ContextLossRecoveryTriggered event.
  4. Send a verification challenge asking the agent to confirm its objective.

A per-terminal cooldown of RECOVERY_COOLDOWN_SECS prevents repeated triggering
if the agent continues to appear disoriented after delivery.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from agentfactor.models.enums import SignalType
from agentfactor.services.context_pack_service import ContextPackService
from agentfactor.services.event_service import EventService

LOG = logging.getLogger(__name__)

SIGNAL_THRESHOLD = 3
WINDOW_SECS = 300.0
RECOVERY_COOLDOWN_SECS = 600.0


class ContextLossDetector:
    """Background task: detects context loss and triggers recovery packs."""

    def __init__(
        self,
        event_service: EventService,
        inbox_service: Any,       # InboxService — avoid circular import
        context_pack_service: ContextPackService,
    ) -> None:
        self._events = event_service
        self._inbox = inbox_service
        self._packs = context_pack_service
        self._last_cursor: int = 0
        self._signal_log: dict[str, list[float]] = {}   # terminal_id → [monotonic timestamps]
        self._last_recovery: dict[str, float] = {}      # terminal_id → monotonic timestamp

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self) -> None:
        import asyncio
        while True:
            try:
                self._tick()
            except Exception:
                LOG.exception("ContextLossDetector tick failed")
            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._scan_loss_signals()
        self._check_all_terminals()

    def _scan_loss_signals(self) -> None:
        """Pull new CONTEXT_LOSS_SIGNAL events and record their timestamps."""
        new_events = self._events.get_recent(
            event_type="TerminalSignal",
            since_cursor=self._last_cursor,
            limit=500,
        )
        if not new_events:
            return
        self._last_cursor = max(e["id"] for e in new_events)

        now = time.monotonic()
        for event in new_events:
            payload = event.get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if payload.get("signal_type") != SignalType.CONTEXT_LOSS_SIGNAL.value:
                continue
            terminal_id = event.get("terminal_id")
            if terminal_id:
                self._signal_log.setdefault(terminal_id, []).append(now)

    def _check_all_terminals(self) -> None:
        """Prune stale signals and trigger recovery if threshold exceeded."""
        now = time.monotonic()
        for terminal_id in list(self._signal_log):
            fresh = [t for t in self._signal_log[terminal_id] if now - t <= WINDOW_SECS]
            self._signal_log[terminal_id] = fresh

            if len(fresh) < SIGNAL_THRESHOLD:
                continue
            last = self._last_recovery.get(terminal_id, 0.0)
            if now - last < RECOVERY_COOLDOWN_SECS:
                continue

            self._trigger_recovery(terminal_id)
            self._last_recovery[terminal_id] = now
            self._signal_log[terminal_id] = []

    def _trigger_recovery(self, terminal_id: str) -> None:
        LOG.info("Context loss detected on terminal %s — building recovery pack", terminal_id)

        try:
            pack = self._packs.build_context_pack(
                terminal_id=terminal_id,
                query="current task objectives progress blockers",
            )
        except Exception:
            LOG.exception("Failed to build recovery context pack for %s", terminal_id)
            return

        summary = _format_pack_for_delivery(pack)
        try:
            self._inbox.queue_message(
                sender_id="system",
                receiver_id=terminal_id,
                message=summary,
                dedupe=False,
            )
        except Exception:
            LOG.exception("Failed to deliver recovery pack to terminal %s", terminal_id)
            return

        self._events.emit(
            type="ContextLossRecoveryTriggered",
            payload={
                "terminal_id": terminal_id,
                "pack_id": pack.get("pack_id"),
                "quality_score": pack.get("quality_score"),
                "signal_count": SIGNAL_THRESHOLD,
            },
            terminal_id=terminal_id,
        )

        challenge = (
            "[CONTEXT_RECOVERY] I noticed you may have lost context. "
            "I've re-sent your current task context above. "
            "Please confirm: what is your current objective and what is your immediate next step?"
        )
        try:
            self._inbox.queue_message(
                sender_id="system",
                receiver_id=terminal_id,
                message=challenge,
                dedupe=False,
            )
        except Exception:
            LOG.debug("Could not send verification challenge to %s", terminal_id, exc_info=True)

        LOG.info(
            "Recovery pack delivered to terminal %s (pack_id=%s, score=%.3f)",
            terminal_id,
            pack.get("pack_id"),
            pack.get("quality_score", 0.0),
        )


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------


def _format_pack_for_delivery(pack: dict[str, Any]) -> str:
    """Render a context pack as a readable plain-text inbox message."""
    lines: list[str] = ["[CONTEXT_PACK] System-delivered context refresh.\n"]

    critical = pack.get("critical", [])
    if critical:
        lines.append("=== CURRENT WORK ===")
        for wi in critical:
            status = wi.get("status", "?").upper()
            lines.append(f"• [{status}] {wi.get('title', '?')}")
            desc = wi.get("description", "")
            if desc:
                lines.append(f"  {desc[:200]}")
            for criterion in wi.get("acceptance_criteria", [])[:3]:
                lines.append(f"  ✓ {criterion}")

    decisions = pack.get("decisions", [])
    if decisions:
        lines.append("\n=== KEY DECISIONS ===")
        for d in decisions[:3]:
            payload = d.get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            excerpt = (
                payload.get("excerpt", str(payload))[:200]
                if isinstance(payload, dict)
                else str(payload)[:200]
            )
            lines.append(f"• {excerpt}")

    history = pack.get("history", [])
    if history:
        lines.append("\n=== RECENT ACTIVITY ===")
        for e in history[:5]:
            ts = str(e.get("timestamp", ""))[:16]
            payload = e.get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            excerpt = payload.get("excerpt", "") if isinstance(payload, dict) else ""
            lines.append(f"• [{ts}] {e.get('type', '?')}: {excerpt[:100]}")

    contradictions = pack.get("contradictions", [])
    if contradictions:
        lines.append(f"\n⚠  {len(contradictions)} potential contradiction(s) — review carefully.")

    return "\n".join(lines)
