"""Causal work graph service — CRUD, critical path, scope conflict detection."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timedelta
from itertools import combinations
from typing import Optional
from uuid import uuid4

from sqlalchemy import select

from agentfactor.clients.database import ProofWindow, WorkEdge, WorkItem, session_scope
from agentfactor.models.enums import EdgeType, ProofType, WorkItemStatus, WorkItemType
from agentfactor.models.work import (
    WorkEdgeCreateRequest,
    WorkEdgeResponse,
    WorkGraphResponse,
    WorkItemCreateRequest,
    WorkItemResponse,
    WorkItemUpdateRequest,
)

LOG = logging.getLogger(__name__)


def _parse_json(val: Optional[str]) -> list:
    try:
        return json.loads(val) if val else []
    except (json.JSONDecodeError, TypeError):
        return []


class ProofRequiredError(ValueError):
    """Raised when a work item cannot transition to DONE without collected proof."""


PROOF_REQUIREMENTS: dict[str, list[ProofType]] = {
    WorkItemType.FEATURE.value:       [ProofType.GIT_COMMIT, ProofType.TEST_PASS],
    WorkItemType.BUGFIX.value:        [ProofType.GIT_COMMIT, ProofType.BUG_NOT_REPRODUCED],
    WorkItemType.REFACTOR.value:      [ProofType.GIT_COMMIT, ProofType.TEST_PASS],
    WorkItemType.TEST.value:          [ProofType.GIT_COMMIT],
    WorkItemType.DOCUMENTATION.value: [ProofType.GIT_COMMIT],
    WorkItemType.REVIEW.value:        [ProofType.REVIEWER_SIGNOFF],
    WorkItemType.INVESTIGATION.value: [ProofType.COMPLETION_SIGNAL],
}

PROOF_TIMEOUTS_MINUTES: dict[str, int] = {
    WorkItemType.FEATURE.value:       45,
    WorkItemType.BUGFIX.value:        20,
    WorkItemType.REFACTOR.value:      30,
    WorkItemType.TEST.value:          15,
    WorkItemType.DOCUMENTATION.value: 15,
    WorkItemType.REVIEW.value:        60,
    WorkItemType.INVESTIGATION.value: 10,
}


class WorkService:
    """Create and query work items in the causal dependency graph."""

    # ------------------------------------------------------------------
    # Work item CRUD
    # ------------------------------------------------------------------

    def create_work_item(self, req: WorkItemCreateRequest) -> WorkItemResponse:
        item_id = f"work_{uuid4().hex[:12]}"
        with session_scope() as db:
            item = WorkItem(
                id=item_id,
                project_id=req.project_id,
                title=req.title,
                description=req.description,
                type=req.type,
                status=WorkItemStatus.READY,
                priority=req.priority,
                owner_terminal_id=req.owner_terminal_id,
                acceptance_criteria=json.dumps(req.acceptance_criteria),
                files_of_interest=json.dumps(req.files_of_interest),
                proof_requirements=json.dumps([p.value for p in req.proof_requirements]) if req.proof_requirements else None,
                complexity=req.complexity,
            )
            db.add(item)
            db.flush()
            db.refresh(item)
            return self._orm_to_response(item)

    def get_work_item(self, item_id: str) -> Optional[WorkItemResponse]:
        with session_scope() as db:
            item = db.get(WorkItem, item_id)
            return self._orm_to_response(item) if item else None

    def list_work_items(
        self,
        project_id: str,
        status: Optional[WorkItemStatus] = None,
    ) -> list[WorkItemResponse]:
        with session_scope() as db:
            q = select(WorkItem).where(WorkItem.project_id == project_id)
            if status is not None:
                q = q.where(WorkItem.status == status)
            rows = db.execute(q).scalars().all()
            return [self._orm_to_response(r) for r in rows]

    def update_work_item(self, item_id: str, req: WorkItemUpdateRequest) -> Optional[WorkItemResponse]:
        with session_scope() as db:
            item = db.get(WorkItem, item_id)
            if item is None:
                return None

            prev_status = item.status
            fields_set = req.model_fields_set

            # Proof gate: DONE requires a closed proof window with collected evidence
            if req.status == WorkItemStatus.DONE and prev_status != WorkItemStatus.DONE:
                proof_reqs = _parse_json(item.proof_requirements)
                if proof_reqs:
                    closed_windows = db.execute(
                        select(ProofWindow).where(
                            ProofWindow.work_item_id == item_id,
                            ProofWindow.status == "closed",
                        )
                    ).scalars().all()
                    proven = any(
                        json.loads(w.proofs_collected or "[]")
                        for w in closed_windows
                    )
                    if not proven:
                        raise ProofRequiredError(
                            f"Work item '{item_id}' has proof requirements "
                            f"({', '.join(proof_reqs)}) but no closed proof window with "
                            "collected evidence. Transition to needs_verification first."
                        )

            if req.title is not None:
                item.title = req.title
            if req.description is not None:
                item.description = req.description
            if req.status is not None:
                item.status = req.status
            if req.priority is not None:
                item.priority = req.priority
            if "owner_terminal_id" in fields_set:
                item.owner_terminal_id = req.owner_terminal_id
            if req.acceptance_criteria is not None:
                item.acceptance_criteria = json.dumps(req.acceptance_criteria)
            if req.files_of_interest is not None:
                item.files_of_interest = json.dumps(req.files_of_interest)
            if "proof_requirements" in fields_set:
                item.proof_requirements = (
                    json.dumps([p.value for p in req.proof_requirements])
                    if req.proof_requirements is not None
                    else None
                )
            if req.complexity is not None:
                item.complexity = req.complexity

            item.updated_at = datetime.utcnow()

            db.flush()
            db.refresh(item)
            result = self._orm_to_response(item)

        # Open a proof window when transitioning to NEEDS_VERIFICATION
        if req.status == WorkItemStatus.NEEDS_VERIFICATION and prev_status != WorkItemStatus.NEEDS_VERIFICATION:
            self.open_proof_window(item_id)

        return result

    def delete_work_item(self, item_id: str) -> bool:
        with session_scope() as db:
            item = db.get(WorkItem, item_id)
            if item is None:
                return False
            db.delete(item)
            return True

    # ------------------------------------------------------------------
    # Agent workflow helpers (claim / complete / block / list)
    # ------------------------------------------------------------------

    def claim_work_item(self, item_id: str, terminal_id: str) -> WorkItemResponse:
        """Atomically claim a READY unclaimed work item. Raises ValueError if not claimable."""
        with session_scope() as db:
            item = db.get(WorkItem, item_id)
            if item is None:
                raise ValueError(f"Work item '{item_id}' not found.")
            if item.status != WorkItemStatus.READY:
                raise ValueError(
                    f"Work item '{item_id}' is not claimable (status={item.status})."
                )
            if item.owner_terminal_id is not None:
                raise ValueError(
                    f"Work item '{item_id}' is already claimed by terminal '{item.owner_terminal_id}'."
                )
            item.status = WorkItemStatus.IN_PROGRESS
            item.owner_terminal_id = terminal_id
            item.updated_at = datetime.utcnow()
            db.flush()
            db.refresh(item)
            return self._orm_to_response(item)

    def list_available_work_items(self, project_id: str) -> list[WorkItemResponse]:
        """Return READY unclaimed items whose dependencies are all DONE."""
        with session_scope() as db:
            candidates = db.execute(
                select(WorkItem).where(
                    WorkItem.project_id == project_id,
                    WorkItem.status == WorkItemStatus.READY,
                    WorkItem.owner_terminal_id.is_(None),
                )
            ).scalars().all()

            done_ids: set[str] = {
                r.id
                for r in db.execute(
                    select(WorkItem).where(
                        WorkItem.project_id == project_id,
                        WorkItem.status == WorkItemStatus.DONE,
                    )
                ).scalars().all()
            }

            result = []
            for item in candidates:
                blockers = db.execute(
                    select(WorkEdge).where(WorkEdge.to_id == item.id)
                ).scalars().all()
                if all(edge.from_id in done_ids for edge in blockers):
                    result.append(self._orm_to_response(item))
            return result

    def list_by_owner(self, terminal_id: str) -> list[WorkItemResponse]:
        """Return all work items currently owned by a terminal."""
        with session_scope() as db:
            rows = db.execute(
                select(WorkItem).where(WorkItem.owner_terminal_id == terminal_id)
            ).scalars().all()
            return [self._orm_to_response(r) for r in rows]

    # ------------------------------------------------------------------
    # Edge CRUD
    # ------------------------------------------------------------------

    def create_edge(self, req: WorkEdgeCreateRequest) -> WorkEdgeResponse:
        with session_scope() as db:
            edge = WorkEdge(
                from_id=req.from_id,
                to_id=req.to_id,
                type=req.type,
                created_by=req.created_by,
                note=req.note,
            )
            db.add(edge)
            db.flush()
            db.refresh(edge)
            return self._edge_orm_to_response(edge)

    def list_edges(self, project_id: str) -> list[WorkEdgeResponse]:
        """Return all edges whose endpoints belong to the given project."""
        with session_scope() as db:
            item_ids = {
                row.id
                for row in db.execute(
                    select(WorkItem.id).where(WorkItem.project_id == project_id)
                ).all()
            }
            rows = db.execute(
                select(WorkEdge).where(WorkEdge.from_id.in_(item_ids))
            ).scalars().all()
            return [self._edge_orm_to_response(r) for r in rows]

    def delete_edge(self, edge_id: int) -> bool:
        with session_scope() as db:
            edge = db.get(WorkEdge, edge_id)
            if edge is None:
                return False
            db.delete(edge)
            return True

    # ------------------------------------------------------------------
    # Proof windows
    # ------------------------------------------------------------------

    def open_proof_window(self, work_item_id: str) -> Optional[dict]:
        with session_scope() as db:
            item = db.get(WorkItem, work_item_id)
            if item is None:
                return None
            timeout_mins = PROOF_TIMEOUTS_MINUTES.get(str(item.type.value if hasattr(item.type, 'value') else item.type), 30)
            expires_at = (datetime.utcnow() + timedelta(minutes=timeout_mins)).isoformat()
            window = ProofWindow(
                work_item_id=work_item_id,
                expires_at=expires_at,
                status="open",
                proofs_collected="[]",
            )
            db.add(window)
            db.flush()
            db.refresh(window)
            return {"id": window.id, "work_item_id": window.work_item_id, "expires_at": window.expires_at}

    def list_proof_windows(self, work_item_id: str) -> list[dict]:
        with session_scope() as db:
            rows = db.execute(
                select(ProofWindow).where(ProofWindow.work_item_id == work_item_id)
            ).scalars().all()
            return [self._proof_window_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Graph queries
    # ------------------------------------------------------------------

    def get_work_graph(self, project_id: str) -> WorkGraphResponse:
        items = self.list_work_items(project_id)
        edges = self.list_edges(project_id)
        critical = self.critical_path(project_id)
        conflicts = self.detect_scope_conflicts(project_id)
        return WorkGraphResponse(
            project_id=project_id,
            work_items=items,
            edges=edges,
            critical_path=critical,
            scope_conflicts=conflicts,
        )

    def critical_path(self, project_id: str) -> list[str]:
        """Return ordered work item IDs on the longest dependency chain (by complexity weight)."""
        with session_scope() as db:
            items = db.execute(
                select(WorkItem).where(
                    WorkItem.project_id == project_id,
                    WorkItem.status.notin_([WorkItemStatus.DONE.value, WorkItemStatus.CANCELLED.value]),
                )
            ).scalars().all()

            if not items:
                return []

            item_ids = {item.id for item in items}
            complexity_map = {item.id: item.complexity for item in items}

            blocking_edges = db.execute(
                select(WorkEdge).where(
                    WorkEdge.type == EdgeType.BLOCKS,
                    WorkEdge.from_id.in_(item_ids),
                    WorkEdge.to_id.in_(item_ids),
                )
            ).scalars().all()

        # Build adjacency
        successors: dict[str, list[str]] = {id: [] for id in item_ids}
        predecessors: dict[str, list[str]] = {id: [] for id in item_ids}
        for edge in blocking_edges:
            successors[edge.from_id].append(edge.to_id)
            predecessors[edge.to_id].append(edge.from_id)

        # Kahn's topological sort
        in_degree = {id: len(predecessors[id]) for id in item_ids}
        queue: deque[str] = deque(id for id in item_ids if in_degree[id] == 0)
        topo: list[str] = []
        while queue:
            node = queue.popleft()
            topo.append(node)
            for succ in successors[node]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if not topo:
            return []  # cycle detected — bail safely

        # DP: longest weighted path
        dist: dict[str, int] = {}
        pred: dict[str, Optional[str]] = {}
        for node_id in topo:
            preds = predecessors[node_id]
            if not preds:
                dist[node_id] = complexity_map[node_id]
                pred[node_id] = None
            else:
                best = max(preds, key=lambda p: dist.get(p, 0))
                dist[node_id] = dist.get(best, 0) + complexity_map[node_id]
                pred[node_id] = best

        end = max(dist, key=lambda k: dist[k])
        path: list[str] = []
        cur: Optional[str] = end
        while cur is not None:
            path.append(cur)
            cur = pred[cur]
        return list(reversed(path))

    def detect_scope_conflicts(self, project_id: str) -> list[dict]:
        """Return pairs of work items from different terminals that share files."""
        with session_scope() as db:
            items = db.execute(
                select(WorkItem).where(
                    WorkItem.project_id == project_id,
                    WorkItem.status.in_([WorkItemStatus.IN_PROGRESS.value, WorkItemStatus.READY.value]),
                    WorkItem.owner_terminal_id.is_not(None),
                )
            ).scalars().all()

            item_ids = {item.id for item in items}
            collab_edges = db.execute(
                select(WorkEdge).where(
                    WorkEdge.type == EdgeType.COLLABORATES_ON,
                    WorkEdge.from_id.in_(item_ids),
                )
            ).scalars().all()

        collab_pairs: set[tuple[str, str]] = set()
        for e in collab_edges:
            collab_pairs.add((e.from_id, e.to_id))
            collab_pairs.add((e.to_id, e.from_id))

        file_owners: dict[str, list[str]] = {}
        item_map = {item.id: item for item in items}
        for item in items:
            for f in json.loads(item.files_of_interest or "[]"):
                file_owners.setdefault(f, []).append(item.id)

        conflicts: list[dict] = []
        for file, owners in file_owners.items():
            if len(owners) < 2:
                continue
            for a, b in combinations(owners, 2):
                ia, ib = item_map[a], item_map[b]
                if ia.owner_terminal_id == ib.owner_terminal_id:
                    continue
                if (a, b) in collab_pairs:
                    continue
                conflicts.append({"item_a": a, "item_b": b, "shared_file": file})
        return conflicts

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _orm_to_response(item: WorkItem) -> WorkItemResponse:
        proof_reqs = _parse_json(item.proof_requirements)
        return WorkItemResponse(
            id=item.id,
            project_id=item.project_id,
            title=item.title,
            description=item.description or "",
            type=WorkItemType(item.type) if not isinstance(item.type, WorkItemType) else item.type,
            status=WorkItemStatus(item.status) if not isinstance(item.status, WorkItemStatus) else item.status,
            priority=item.priority,
            owner_terminal_id=item.owner_terminal_id,
            acceptance_criteria=_parse_json(item.acceptance_criteria),
            files_of_interest=_parse_json(item.files_of_interest),
            proof_requirements=[ProofType(p) for p in proof_reqs] if proof_reqs else None,
            complexity=item.complexity,
            created_at=str(item.created_at),
            updated_at=str(item.updated_at),
        )

    @staticmethod
    def _edge_orm_to_response(edge: WorkEdge) -> WorkEdgeResponse:
        from agentfactor.models.work import WorkEdgeResponse
        return WorkEdgeResponse(
            id=edge.id,
            from_id=edge.from_id,
            to_id=edge.to_id,
            type=EdgeType(edge.type) if not isinstance(edge.type, EdgeType) else edge.type,
            created_by=edge.created_by,
            note=edge.note,
            created_at=str(edge.created_at),
        )

    @staticmethod
    def _proof_window_to_dict(window: ProofWindow) -> dict:
        try:
            proofs = json.loads(window.proofs_collected or "[]")
        except (json.JSONDecodeError, TypeError):
            proofs = []
        return {
            "id": window.id,
            "work_item_id": window.work_item_id,
            "opened_at": str(window.opened_at),
            "expires_at": str(window.expires_at),
            "status": window.status,
            "proofs_collected": proofs,
            "closed_at": str(window.closed_at) if window.closed_at else None,
        }
