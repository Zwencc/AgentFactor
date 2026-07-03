"""Topology Engine — generates team restructuring proposals from real performance metrics.

Runs every 120 seconds. For each active terminal it fetches the most recent
METRICS_WINDOW_SAMPLES TerminalMetrics rows and checks three conditions:

  STALL     — idle_streak_minutes >= STALL_MINUTES with open work items
              → proposal_type="investigate"
  HIGH_ERROR — error_density >= ERROR_THRESHOLD across all recent samples
              → proposal_type="replace_provider" (with Thompson-sampled suggestion)
  LOW_VELOCITY — output_velocity_tpm < LOW_VELOCITY_TPM across all recent samples
               → proposal_type="add_worker"

Each detected condition produces a TopologyProposal that is:
  1. Persisted to the topology_proposals table.
  2. Delivered to all supervisor-role terminals via inbox.
  3. Emitted as a TopologyProposalCreated event.

A per-terminal cooldown of PROPOSAL_COOLDOWN_SECS prevents proposal floods.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import desc, select

from agentfactor.clients.database import Terminal, TerminalMetrics, TopologyProposal, WorkItem, session_scope
from agentfactor.models.enums import WorkItemStatus
from agentfactor.services.capability_registry import CapabilityRegistry
from agentfactor.services.event_service import EventService

LOG = logging.getLogger(__name__)

STALL_MINUTES = 15.0
ERROR_THRESHOLD = 0.30
LOW_VELOCITY_TPM = 10.0
METRICS_WINDOW_SAMPLES = 3
PROPOSAL_COOLDOWN_SECS = 600.0


class TopologyEngine:
    """Background task: detects under-performing terminals and proposes topology changes."""

    def __init__(
        self,
        event_service: EventService,
        capability_registry: CapabilityRegistry,
        inbox_service: Any,
    ) -> None:
        self._events = event_service
        self._registry = capability_registry
        self._inbox = inbox_service
        self._last_proposal: dict[str, float] = {}  # terminal_id → monotonic timestamp

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self) -> None:
        import asyncio
        while True:
            try:
                self._tick()
            except Exception:
                LOG.exception("TopologyEngine tick failed")
            await asyncio.sleep(120)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        for terminal in self._get_active_terminals():
            self._evaluate_terminal(terminal)

    def _evaluate_terminal(self, terminal: dict) -> None:
        terminal_id = terminal["id"]
        now = time.monotonic()
        if now - self._last_proposal.get(terminal_id, 0.0) < PROPOSAL_COOLDOWN_SECS:
            return

        metrics = self._get_recent_metrics(terminal_id, n=METRICS_WINDOW_SAMPLES)
        if not metrics:
            return

        proposal = self._detect_condition(terminal, metrics)
        if proposal:
            self._emit_proposal(proposal)
            self._last_proposal[terminal_id] = now

    # ------------------------------------------------------------------
    # Data fetchers
    # ------------------------------------------------------------------

    def _get_active_terminals(self) -> list[dict]:
        with session_scope() as db:
            rows = db.execute(select(Terminal)).scalars().all()
            return [
                {
                    "id": t.id,
                    "provider": t.provider,
                    "agent_profile": t.agent_profile or "",
                    "status": str(t.status.value if hasattr(t.status, "value") else t.status),
                }
                for t in rows
            ]

    def _get_recent_metrics(self, terminal_id: str, n: int) -> list[dict]:
        with session_scope() as db:
            rows = db.execute(
                select(TerminalMetrics)
                .where(TerminalMetrics.terminal_id == terminal_id)
                .order_by(desc(TerminalMetrics.sampled_at))
                .limit(n)
            ).scalars().all()
            return [
                {
                    "sampled_at": str(r.sampled_at),
                    "output_velocity_tpm": r.output_velocity_tpm,
                    "error_density": r.error_density,
                    "idle_streak_minutes": r.idle_streak_minutes,
                    "signal_counts": r.signal_counts,
                }
                for r in rows
            ]

    def _count_open_work_items(self, terminal_id: str) -> int:
        with session_scope() as db:
            rows = db.execute(
                select(WorkItem).where(
                    WorkItem.owner_terminal_id == terminal_id,
                    WorkItem.status.notin_([
                        WorkItemStatus.DONE.value,
                        WorkItemStatus.CANCELLED.value,
                    ]),
                )
            ).scalars().all()
            return len(rows)

    # ------------------------------------------------------------------
    # Condition detection
    # ------------------------------------------------------------------

    def _detect_condition(self, terminal: dict, metrics: list[dict]) -> Optional[dict]:
        terminal_id = terminal["id"]
        latest = metrics[0]

        # STALL: idle too long with unfinished work
        if latest["idle_streak_minutes"] >= STALL_MINUTES:
            open_count = self._count_open_work_items(terminal_id)
            if open_count > 0:
                return _make_proposal(
                    terminal_id=terminal_id,
                    proposal_type="investigate",
                    reason=(
                        f"Terminal idle for {latest['idle_streak_minutes']:.1f} min "
                        f"with {open_count} open work item(s). Possible stall."
                    ),
                    suggested_provider=None,
                    suggested_persona=None,
                    metrics_snapshot=latest,
                )

        # HIGH ERROR: all recent samples above error threshold
        if len(metrics) >= METRICS_WINDOW_SAMPLES and all(
            m["error_density"] >= ERROR_THRESHOLD for m in metrics
        ):
            best = self._recommend_replacement(terminal, "bugfix")
            return _make_proposal(
                terminal_id=terminal_id,
                proposal_type="replace_provider",
                reason=(
                    f"Error density {latest['error_density']:.2f} sustained over "
                    f"{len(metrics)} samples (threshold {ERROR_THRESHOLD})."
                ),
                suggested_provider=best[0] if best else None,
                suggested_persona=best[1] if best else None,
                metrics_snapshot=latest,
            )

        # LOW VELOCITY: all recent samples below velocity floor (but not stalled)
        if (
            all(m["output_velocity_tpm"] < LOW_VELOCITY_TPM for m in metrics)
            and latest["idle_streak_minutes"] < 5.0
        ):
            return _make_proposal(
                terminal_id=terminal_id,
                proposal_type="add_worker",
                reason=(
                    f"Output velocity {latest['output_velocity_tpm']:.1f} TPM below "
                    f"threshold {LOW_VELOCITY_TPM} for {len(metrics)} samples."
                ),
                suggested_provider=None,
                suggested_persona=None,
                metrics_snapshot=latest,
            )

        return None

    def _recommend_replacement(
        self, terminal: dict, task_type: str
    ) -> Optional[tuple[str, str]]:
        """Thompson-sample from other known provider/persona pairs."""
        candidates = self._get_other_candidates(terminal["id"])
        if not candidates:
            return None
        ranked = self._registry.recommend(task_type, candidates, n=1)
        if not ranked:
            return None
        return ranked[0][0], ranked[0][1]

    def _get_other_candidates(self, exclude_terminal_id: str) -> list[tuple[str, str]]:
        with session_scope() as db:
            rows = db.execute(
                select(Terminal).where(Terminal.id != exclude_terminal_id)
            ).scalars().all()
            seen: set[tuple[str, str]] = set()
            candidates: list[tuple[str, str]] = []
            for r in rows:
                key = (r.provider, r.agent_profile or "")
                if key not in seen:
                    seen.add(key)
                    candidates.append(key)
        return candidates

    # ------------------------------------------------------------------
    # Proposal persistence + delivery
    # ------------------------------------------------------------------

    def _emit_proposal(self, proposal: dict) -> None:
        terminal_id = proposal["terminal_id"]

        with session_scope() as db:
            db.add(TopologyProposal(
                id=proposal["id"],
                terminal_id=terminal_id,
                proposal_type=proposal["proposal_type"],
                reason=proposal["reason"],
                suggested_provider=proposal.get("suggested_provider"),
                suggested_persona=proposal.get("suggested_persona"),
                metrics_snapshot=json.dumps(proposal["metrics_snapshot"]),
                status="pending",
            ))

        self._events.emit(
            type="TopologyProposalCreated",
            payload={
                "proposal_id": proposal["id"],
                "terminal_id": terminal_id,
                "proposal_type": proposal["proposal_type"],
                "reason": proposal["reason"],
            },
            terminal_id=terminal_id,
        )

        self._deliver_to_supervisors(proposal)

        LOG.info(
            "Topology proposal %s (%s) for terminal %s: %s",
            proposal["id"], proposal["proposal_type"], terminal_id, proposal["reason"],
        )

    def _deliver_to_supervisors(self, proposal: dict) -> None:
        msg_lines = [
            f"[TOPOLOGY] Proposal {proposal['id']} ({proposal['proposal_type']})"
            f" for terminal {proposal['terminal_id']}:",
            proposal["reason"],
        ]
        if proposal.get("suggested_provider"):
            msg_lines.append(
                f"Suggested: provider={proposal['suggested_provider']}"
                f", persona={proposal['suggested_persona']}"
            )
        msg = "\n".join(msg_lines)

        try:
            with session_scope() as db:
                supervisors = db.execute(
                    select(Terminal).where(Terminal.agent_profile.like("%supervisor%"))
                ).scalars().all()
                sup_ids = [s.id for s in supervisors]
        except Exception:
            LOG.debug("Could not look up supervisors for topology delivery", exc_info=True)
            return

        for sup_id in sup_ids:
            try:
                self._inbox.queue_message(
                    sender_id="system",
                    receiver_id=sup_id,
                    message=msg,
                    dedupe=False,
                )
            except Exception:
                LOG.debug("Could not deliver topology proposal to supervisor %s", sup_id, exc_info=True)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _make_proposal(
    terminal_id: str,
    proposal_type: str,
    reason: str,
    suggested_provider: Optional[str],
    suggested_persona: Optional[str],
    metrics_snapshot: dict,
) -> dict:
    return {
        "id": f"prop_{uuid4().hex[:10]}",
        "terminal_id": terminal_id,
        "proposal_type": proposal_type,
        "reason": reason,
        "suggested_provider": suggested_provider,
        "suggested_persona": suggested_persona,
        "metrics_snapshot": metrics_snapshot,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }
