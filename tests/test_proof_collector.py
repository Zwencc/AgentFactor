from datetime import datetime, timedelta

from agentfactor.clients.database import ProofWindow, WorkItem, session_scope
from agentfactor.models.enums import ProofType, WorkItemStatus, WorkItemType
from agentfactor.services.event_service import EventService
from agentfactor.services.proof_collector import ProofCollector
from agentfactor.services.work_service import WorkService


def _work_item(item_id: str) -> WorkItem:
    return WorkItem(
        id=item_id,
        project_id="proof-collector",
        title="Collect proof",
        type=WorkItemType.INVESTIGATION,
        status=WorkItemStatus.NEEDS_VERIFICATION,
        proof_requirements='["completion_signal"]',
    )


def test_add_proof_updates_datetime_column_with_datetime_value():
    with session_scope() as db:
        db.add(_work_item("work-proof-complete"))
        window = ProofWindow(
            work_item_id="work-proof-complete",
            expires_at=(datetime.utcnow() + timedelta(minutes=10)).isoformat(),
        )
        db.add(window)
        db.flush()
        window_id = window.id

    collector = ProofCollector(EventService(), None, WorkService())
    collector._add_proof(window_id, {"type": ProofType.COMPLETION_SIGNAL.value})

    with session_scope() as db:
        item = db.get(WorkItem, "work-proof-complete")
        stored_window = db.get(ProofWindow, window_id)
        assert item.status == WorkItemStatus.DONE
        assert isinstance(item.updated_at, datetime)
        assert stored_window.status == "closed"


def test_expire_window_updates_datetime_column_with_datetime_value():
    with session_scope() as db:
        db.add(_work_item("work-proof-expired"))
        window = ProofWindow(
            work_item_id="work-proof-expired",
            expires_at=(datetime.utcnow() - timedelta(minutes=1)).isoformat(),
        )
        db.add(window)
        db.flush()
        window_id = window.id

    collector = ProofCollector(EventService(), None, WorkService())
    collector._handle_expirations()

    with session_scope() as db:
        item = db.get(WorkItem, "work-proof-expired")
        stored_window = db.get(ProofWindow, window_id)
        assert item.status == WorkItemStatus.IN_PROGRESS
        assert isinstance(item.updated_at, datetime)
        assert stored_window.status == "expired"
