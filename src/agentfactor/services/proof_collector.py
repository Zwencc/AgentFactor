"""Proof Collector — background task that watches for verifiable evidence of work item completion.

Lifecycle per proof window:
  1. Work item transitions to NEEDS_VERIFICATION → proof window opened.
  2. Collector checks every 30 s:
     a. New TerminalSignal events (TEST_RESULT, COMPLETION_SIGNAL) from the assigned terminal.
     b. Git log for commits that match the work item since the window opened.
  3. If required proof types are all collected → window closed, work item → DONE.
  4. If expires_at passes → window expired, work item regresses to IN_PROGRESS.
     Assigned terminal receives an inbox notification.
"""

from __future__ import annotations

import difflib
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from agentfactor.clients.database import ProofWindow, Terminal, WorkItem, session_scope
from agentfactor.models.enums import ProofType, SignalType, WorkItemStatus
from agentfactor.services.event_service import EventService
from agentfactor.services.work_service import PROOF_REQUIREMENTS

LOG = logging.getLogger(__name__)


class ProofCollector:
    """Background task: collects proof for open proof windows and handles timeouts."""

    def __init__(
        self,
        event_service: EventService,
        inbox_service,          # InboxService — avoid circular import
        work_service,           # WorkService
        capability_registry=None,  # CapabilityRegistry — optional, avoid circular import
    ) -> None:
        self._events = event_service
        self._inbox = inbox_service
        self._work = work_service
        self._capability_registry = capability_registry
        self._last_event_cursor: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self) -> None:
        import asyncio

        while True:
            try:
                self._tick()
            except Exception:
                LOG.exception("ProofCollector tick failed")
            await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._scan_terminal_signals()
        self._check_git_proofs()
        self._handle_expirations()

    def _scan_terminal_signals(self) -> None:
        """Scan new TerminalSignal events and try to match them to open proof windows."""
        new_events = self._events.get_recent(
            event_type="TerminalSignal",
            since_cursor=self._last_event_cursor,
            limit=200,
        )
        if not new_events:
            return
        self._last_event_cursor = max(e["id"] for e in new_events)

        for event in new_events:
            payload = event.get("payload", {})
            signal_type = payload.get("signal_type")
            if signal_type not in (SignalType.TEST_RESULT.value, SignalType.COMPLETION_SIGNAL.value):
                continue
            terminal_id = event.get("terminal_id")
            if not terminal_id:
                continue
            self._evaluate_signal_for_terminal(terminal_id, signal_type, payload)

    def _evaluate_signal_for_terminal(self, terminal_id: str, signal_type: str, payload: dict) -> None:
        with session_scope() as db:
            rows = db.execute(
                select(ProofWindow)
                .join(WorkItem, WorkItem.id == ProofWindow.work_item_id)
                .where(
                    ProofWindow.status == "open",
                    WorkItem.owner_terminal_id == terminal_id,
                )
            ).scalars().all()
            window_ids = [w.id for w in rows]

        for window_id in window_ids:
            proof = self._signal_to_proof(signal_type, payload)
            if proof:
                self._add_proof(window_id, proof)

    def _signal_to_proof(self, signal_type: str, payload: dict) -> Optional[dict]:
        now = datetime.utcnow().isoformat()
        if signal_type == SignalType.TEST_RESULT.value:
            groups = payload.get("groups", [])
            passed = int(groups[0]) if groups else 0
            if passed > 0:
                return {
                    "type": ProofType.TEST_PASS.value,
                    "passed_count": passed,
                    "excerpt": payload.get("excerpt", "")[:200],
                    "received_at": now,
                }
        elif signal_type == SignalType.COMPLETION_SIGNAL.value:
            return {
                "type": ProofType.COMPLETION_SIGNAL.value,
                "excerpt": payload.get("excerpt", "")[:200],
                "received_at": now,
            }
        return None

    # ------------------------------------------------------------------
    # Git-based proof
    # ------------------------------------------------------------------

    def _check_git_proofs(self) -> None:
        with session_scope() as db:
            windows = db.execute(
                select(ProofWindow).where(ProofWindow.status == "open")
            ).scalars().all()
            window_ids = [w.id for w in windows]

        for wid in window_ids:
            self._check_git_for_window(wid)

    def _check_git_for_window(self, window_id: int) -> None:
        with session_scope() as db:
            window = db.get(ProofWindow, window_id)
            if window is None or window.status != "open":
                return
            work_item = db.get(WorkItem, window.work_item_id)
            if work_item is None:
                return
            opened_at = str(window.opened_at)
            item_id = work_item.id
            item_title = work_item.title
            files_of_interest = json.loads(work_item.files_of_interest or "[]")

        try:
            result = subprocess.run(
                [
                    "git", "log",
                    f"--since={opened_at}",
                    "--format=%H%x00%s%x00%b%x00",
                    "--name-only",
                    "--diff-filter=AM",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(Path.cwd()),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return

        if result.returncode != 0 or not result.stdout.strip():
            return

        for commit in self._parse_git_log(result.stdout):
            if self._matches_work_item(commit, item_id, item_title, files_of_interest):
                proof = {
                    "type": ProofType.GIT_COMMIT.value,
                    "hash": commit["hash"],
                    "message": commit["subject"],
                    "files": commit["files"],
                    "received_at": datetime.utcnow().isoformat(),
                }
                self._add_proof(window_id, proof)
                break  # one matching commit is enough per tick

    @staticmethod
    def _parse_git_log(output: str) -> list[dict]:
        """Parse the NUL-delimited git log output into commit dicts."""
        commits = []
        # Each commit block ends with a NUL; file list follows on subsequent lines
        raw_blocks = output.split("\x00\n")
        i = 0
        while i < len(raw_blocks):
            header = raw_blocks[i].strip()
            if not header:
                i += 1
                continue
            parts = header.split("\x00")
            hash_str = parts[0].strip() if len(parts) > 0 else ""
            subject = parts[1].strip() if len(parts) > 1 else ""
            body = parts[2].strip() if len(parts) > 2 else ""
            # File names are in the next block before the next commit
            files: list[str] = []
            if i + 1 < len(raw_blocks):
                next_block = raw_blocks[i + 1]
                if next_block and "\x00" not in next_block:
                    files = [f for f in next_block.splitlines() if f.strip()]
                    i += 1
            if hash_str:
                commits.append({"hash": hash_str, "subject": subject, "body": body, "files": files})
            i += 1
        return commits

    @staticmethod
    def _matches_work_item(
        commit: dict,
        item_id: str,
        item_title: str,
        files_of_interest: list[str],
    ) -> bool:
        msg = f"{commit['subject']} {commit['body']}"
        if item_id in msg:
            return True
        if files_of_interest and commit["files"]:
            overlap = set(commit["files"]) & set(files_of_interest)
            if len(overlap) / len(files_of_interest) > 0.5:
                return True
        ratio = difflib.SequenceMatcher(None, item_title.lower(), commit["subject"].lower()).ratio()
        return ratio > 0.6

    # ------------------------------------------------------------------
    # Proof sufficiency + window management
    # ------------------------------------------------------------------

    def _add_proof(self, window_id: int, proof: dict) -> None:
        outcome_data: Optional[tuple[str, str, str]] = None  # (provider, persona, task_type)
        with session_scope() as db:
            window = db.get(ProofWindow, window_id)
            if window is None or window.status != "open":
                return
            proofs: list[dict] = json.loads(window.proofs_collected or "[]")

            # Dedup by proof type — don't add the same type twice
            existing_types = {p["type"] for p in proofs}
            if proof["type"] in existing_types:
                return

            proofs.append(proof)
            window.proofs_collected = json.dumps(proofs)

            work_item = db.get(WorkItem, window.work_item_id)
            if work_item is None:
                return

            required = self._required_proofs(work_item)
            collected_types = {p["type"] for p in proofs}
            sufficient = all(r.value in collected_types for r in required)

            if sufficient:
                window.status = "closed"
                window.closed_at = datetime.utcnow().isoformat()
                work_item.status = WorkItemStatus.DONE
                work_item.updated_at = datetime.utcnow().isoformat()

                # Capture for capability recording after session closes
                if work_item.owner_terminal_id:
                    terminal = db.get(Terminal, work_item.owner_terminal_id)
                    if terminal:
                        task_type = str(
                            work_item.type.value
                            if hasattr(work_item.type, "value")
                            else work_item.type
                        )
                        outcome_data = (terminal.provider, terminal.agent_profile or "", task_type)

                self._events.emit(
                    type="WorkItemProofReceived",
                    payload={
                        "work_item_id": window.work_item_id,
                        "proof_window_id": window.id,
                        "proofs": proofs,
                    },
                )
                self._events.emit(
                    type="WorkItemStatusChanged",
                    payload={
                        "work_item_id": window.work_item_id,
                        "from_status": WorkItemStatus.NEEDS_VERIFICATION.value,
                        "to_status": WorkItemStatus.DONE.value,
                    },
                )
                LOG.info("WorkItem %s marked DONE via proof window %s", window.work_item_id, window.id)

        if outcome_data is not None and self._capability_registry is not None:
            provider, persona, task_type = outcome_data
            try:
                self._capability_registry.record_outcome(provider, persona, task_type, success=True)
            except Exception:
                LOG.debug("Failed to record capability outcome for %s/%s/%s", provider, persona, task_type, exc_info=True)

    def _handle_expirations(self) -> None:
        now = datetime.utcnow()
        with session_scope() as db:
            windows = db.execute(
                select(ProofWindow).where(ProofWindow.status == "open")
            ).scalars().all()
            window_ids = [w.id for w in windows]
            expires_map = {w.id: str(w.expires_at) for w in windows}

        for wid in window_ids:
            try:
                expires_at = datetime.fromisoformat(expires_map[wid].split(".")[0])
            except (ValueError, AttributeError):
                continue
            if now > expires_at:
                self._expire_window(wid)

    def _expire_window(self, window_id: int) -> None:
        with session_scope() as db:
            window = db.get(ProofWindow, window_id)
            if window is None or window.status != "open":
                return
            window.status = "expired"
            window.closed_at = datetime.utcnow().isoformat()

            work_item = db.get(WorkItem, window.work_item_id)
            if work_item is not None:
                work_item.status = WorkItemStatus.IN_PROGRESS
                work_item.updated_at = datetime.utcnow().isoformat()
                owner = work_item.owner_terminal_id
                title = work_item.title
                wid_str = window.work_item_id

        self._events.emit(
            type="ProofCollectionTimeout",
            payload={"work_item_id": wid_str, "proof_window_id": window_id},
        )
        if owner and self._inbox:
            try:
                self._inbox.queue_message(
                    sender_id="system",
                    receiver_id=owner,
                    message=(
                        f"[PROOF_TIMEOUT] Verification window expired for '{title}' ({wid_str}). "
                        "Please re-confirm completion with evidence."
                    ),
                )
            except Exception:
                LOG.debug("Could not send proof timeout inbox message to %s", owner, exc_info=True)
        LOG.info("ProofWindow %s expired; work item %s regressed to IN_PROGRESS", window_id, wid_str)

    @staticmethod
    def _required_proofs(work_item: WorkItem) -> list[ProofType]:
        if work_item.proof_requirements:
            try:
                return [ProofType(r) for r in json.loads(work_item.proof_requirements)]
            except (json.JSONDecodeError, ValueError):
                pass
        item_type = work_item.type.value if hasattr(work_item.type, "value") else str(work_item.type)
        return PROOF_REQUIREMENTS.get(item_type, [ProofType.COMPLETION_SIGNAL])
