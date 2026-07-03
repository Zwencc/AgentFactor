"""Capability Registry — Bayesian Beta distribution tracking for provider/persona performance.

Each (provider, persona, task_type) triple maintains Beta(alpha, beta_param) posterior:
  - alpha     = 1 + cumulative successes
  - beta_param = 1 + cumulative failures

Thompson sampling: draw a random sample from each candidate's posterior; pick the highest.
Cold-start cross-persona transfer: when no row exists for a new (provider, persona, task_type),
  seed alpha/beta at half the average of existing rows for the same provider+task_type so the
  new entry starts informed but with lower confidence than established entries.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from agentfactor.clients.database import CapabilityEstimate, session_scope

LOG = logging.getLogger(__name__)


class CapabilityRegistry:
    """Bayesian capability tracking and Thompson-sampling–based recommendation."""

    # ------------------------------------------------------------------
    # Outcome recording
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        provider: str,
        persona: str,
        task_type: str,
        success: bool,
    ) -> None:
        """Update the Beta posterior for (provider, persona, task_type) with one observation."""
        with session_scope() as db:
            row = db.execute(
                select(CapabilityEstimate).where(
                    CapabilityEstimate.provider == provider,
                    CapabilityEstimate.persona == persona,
                    CapabilityEstimate.task_type == task_type,
                )
            ).scalar_one_or_none()

            if row is None:
                alpha0, beta0 = _cold_start_prior(db, provider, task_type)
                row = CapabilityEstimate(
                    provider=provider,
                    persona=persona,
                    task_type=task_type,
                    alpha=alpha0,
                    beta_param=beta0,
                    total_attempts=0,
                )
                db.add(row)
                db.flush()

            if success:
                row.alpha += 1.0
            else:
                row.beta_param += 1.0
            row.total_attempts += 1
            row.last_updated = datetime.utcnow().isoformat()

        LOG.debug(
            "Capability outcome recorded: %s/%s/%s success=%s",
            provider, persona, task_type, success,
        )

    # ------------------------------------------------------------------
    # Sampling and recommendation
    # ------------------------------------------------------------------

    def thompson_sample(
        self,
        provider: str,
        persona: str,
        task_type: str,
    ) -> float:
        """Draw a single Thompson sample from Beta(alpha, beta_param) for this triple."""
        with session_scope() as db:
            row = db.execute(
                select(CapabilityEstimate).where(
                    CapabilityEstimate.provider == provider,
                    CapabilityEstimate.persona == persona,
                    CapabilityEstimate.task_type == task_type,
                )
            ).scalar_one_or_none()
        alpha = row.alpha if row else 1.0
        beta = row.beta_param if row else 1.0
        return random.betavariate(alpha, beta)

    def recommend(
        self,
        task_type: str,
        candidates: list[tuple[str, str]],
        n: int = 1,
    ) -> list[tuple[str, str, float]]:
        """Return top-n candidates ranked by Thompson sampling score.

        candidates: list of (provider, persona) pairs.
        Returns list of (provider, persona, score) sorted descending.
        """
        scored = [
            (provider, persona, self.thompson_sample(provider, persona, task_type))
            for provider, persona in candidates
        ]
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:n]

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def get_estimate(
        self,
        provider: str,
        persona: str,
        task_type: str,
    ) -> dict:
        """Return the current estimate dict for the triple (never raises)."""
        with session_scope() as db:
            row = db.execute(
                select(CapabilityEstimate).where(
                    CapabilityEstimate.provider == provider,
                    CapabilityEstimate.persona == persona,
                    CapabilityEstimate.task_type == task_type,
                )
            ).scalar_one_or_none()
        if row is None:
            return {
                "provider": provider,
                "persona": persona,
                "task_type": task_type,
                "alpha": 1.0,
                "beta_param": 1.0,
                "total_attempts": 0,
                "mean": 0.5,
                "last_updated": None,
            }
        return _row_to_dict(row)

    def list_estimates(self) -> list[dict]:
        """Return all stored capability estimates ordered by provider/persona/task_type."""
        with session_scope() as db:
            rows = db.execute(
                select(CapabilityEstimate).order_by(
                    CapabilityEstimate.provider,
                    CapabilityEstimate.persona,
                    CapabilityEstimate.task_type,
                )
            ).scalars().all()
            return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _cold_start_prior(db, provider: str, task_type: str) -> tuple[float, float]:
    """Seed a new row at half the mean alpha/beta of same-provider, same-task peers."""
    peers = db.execute(
        select(CapabilityEstimate).where(
            CapabilityEstimate.provider == provider,
            CapabilityEstimate.task_type == task_type,
        )
    ).scalars().all()
    if not peers:
        return 1.0, 1.0
    mean_alpha = sum(p.alpha for p in peers) / len(peers)
    mean_beta = sum(p.beta_param for p in peers) / len(peers)
    # Half-strength transfer: informed but less confident than established entries
    return max(1.0, mean_alpha * 0.5), max(1.0, mean_beta * 0.5)


def _row_to_dict(row: CapabilityEstimate) -> dict:
    mean = row.alpha / (row.alpha + row.beta_param)
    return {
        "provider": row.provider,
        "persona": row.persona,
        "task_type": row.task_type,
        "alpha": row.alpha,
        "beta_param": row.beta_param,
        "total_attempts": row.total_attempts,
        "mean": round(mean, 4),
        "last_updated": str(row.last_updated),
    }
