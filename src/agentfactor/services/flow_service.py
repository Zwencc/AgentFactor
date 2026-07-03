"""Scheduled flow service."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from croniter import croniter

from agentfactor.clients.database import Flow as FlowORM, session_scope
from agentfactor.models.flow import Flow


class FlowService:
    """Persists and manages flow definitions."""

    def register_flow(
        self,
        name: str,
        file_path: str,
        schedule: str,
        agent_profile: str,
        script: Optional[str] = None,
    ) -> Flow:
        flow = FlowORM(
            name=name,
            file_path=file_path,
            schedule=schedule,
            agent_profile=agent_profile,
            script=script,
            enabled=True,
            next_run=self.compute_next_run(schedule),
        )
        with session_scope() as db:
            merged = db.merge(flow)
            db.flush()
            db.refresh(merged)
            return Flow.model_validate(merged, from_attributes=True)

    def list_flows(self) -> List[Flow]:
        with session_scope() as db:
            flows = db.query(FlowORM).order_by(FlowORM.name.asc()).all()
            return [Flow.model_validate(obj, from_attributes=True) for obj in flows]

    def get_flow(self, name: str) -> Optional[Flow]:
        with session_scope() as db:
            flow = db.get(FlowORM, name)
            return Flow.model_validate(flow, from_attributes=True) if flow else None

    def set_enabled(self, name: str, enabled: bool) -> None:
        with session_scope() as db:
            flow = db.get(FlowORM, name)
            if flow:
                flow.enabled = enabled
                if enabled and flow.next_run is None:
                    flow.next_run = self.compute_next_run(flow.schedule)

    def delete_flow(self, name: str) -> None:
        with session_scope() as db:
            flow = db.get(FlowORM, name)
            if flow:
                db.delete(flow)

    def record_run(self, name: str, *, last_run: datetime, next_run: Optional[datetime]) -> None:
        with session_scope() as db:
            flow = db.get(FlowORM, name)
            if flow:
                flow.last_run = last_run
                flow.next_run = next_run

    @staticmethod
    def compute_next_run(schedule: str, base_time: Optional[datetime] = None) -> datetime:
        return croniter(schedule, base_time or datetime.utcnow()).get_next(datetime)
