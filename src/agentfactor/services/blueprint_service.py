"""Blueprint generation and import service for the auto work graph feature."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from sqlalchemy import distinct, select

from agentfactor import constants
from agentfactor.clients.database import BlueprintJob, session_scope
from agentfactor.models.enums import EdgeType, ProofType, WorkItemType
from agentfactor.models.work import WorkEdgeCreateRequest, WorkItemCreateRequest, WorkItemResponse
from agentfactor.services.work_service import WorkService

LOG = logging.getLogger(__name__)

_TOML_FENCE_RE = re.compile(r"```toml\s*\n(.*?)```", re.DOTALL)
_SENTINEL_RE = re.compile(
    r"<<<CONDUCTOR_BLUEPRINT_BEGIN>>>\s*\n(.*?)<<<CONDUCTOR_BLUEPRINT_END>>>",
    re.DOTALL,
)


class BlueprintParseError(ValueError):
    """TOML content is syntactically valid but violates the blueprint schema."""


@dataclass
class BlueprintItem:
    id: str
    title: str
    description: str
    category: str
    priority: int
    effort_hours: float
    depends_on: list[str]
    proof_requirements: list[str]
    acceptance_criteria: list[str]
    files_of_interest: list[str] = field(default_factory=list)


class BlueprintService:
    def __init__(self) -> None:
        self._work = WorkService()

    # ------------------------------------------------------------------
    # Parse + validate
    # ------------------------------------------------------------------

    def parse_toml(self, content: str) -> tuple[dict, list[BlueprintItem]]:
        """Return (meta, items). Raises BlueprintParseError on schema violations."""
        try:
            doc = tomllib.loads(content)
        except Exception as exc:
            raise BlueprintParseError(f"Invalid TOML: {exc}") from exc

        meta = doc.get("meta", {})
        raw_items = doc.get("items", [])
        if not isinstance(raw_items, list) or len(raw_items) == 0:
            raise BlueprintParseError("Blueprint must contain at least one [[items]] entry")

        ids_seen: set[str] = set()
        items: list[BlueprintItem] = []
        for i, raw in enumerate(raw_items):
            item_id = raw.get("id", "").strip()
            title = raw.get("title", "").strip()
            if not item_id:
                raise BlueprintParseError(f"items[{i}] missing required field 'id'")
            if not title:
                raise BlueprintParseError(f"items[{i}] (id={item_id!r}) missing required field 'title'")
            if item_id in ids_seen:
                raise BlueprintParseError(f"Duplicate item id: {item_id!r}")
            ids_seen.add(item_id)
            items.append(BlueprintItem(
                id=item_id,
                title=title,
                description=raw.get("description", ""),
                category=raw.get("category", "feature"),
                priority=int(raw.get("priority", 3)),
                effort_hours=float(raw.get("effort_hours", 0)),
                depends_on=list(raw.get("depends_on", [])),
                proof_requirements=list(raw.get("proof_requirements", [])),
                acceptance_criteria=list(raw.get("acceptance_criteria", [])),
                files_of_interest=list(raw.get("files_of_interest", [])),
            ))

        for item in items:
            for dep in item.depends_on:
                if dep not in ids_seen:
                    raise BlueprintParseError(
                        f"Item {item.id!r} depends_on unknown id {dep!r}"
                    )

        return meta, items

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def save_blueprint(self, terminal_id: str, project_id: str, toml_content: str) -> int:
        """Validate + persist blueprint to DB and file. Returns BlueprintJob.id."""
        self.parse_toml(toml_content)  # raises BlueprintParseError on invalid

        blueprints_dir = Path(constants.BLUEPRINTS_DIR) / project_id
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        (blueprints_dir / f"{terminal_id}.toml").write_text(toml_content, encoding="utf-8")

        with session_scope() as db:
            existing = db.execute(
                select(BlueprintJob).where(BlueprintJob.terminal_id == terminal_id)
            ).scalar_one_or_none()
            if existing:
                existing.toml_content = toml_content
                existing.status = "ready"
                db.flush()
                db.refresh(existing)
                return existing.id
            job = BlueprintJob(
                terminal_id=terminal_id,
                project_id=project_id,
                toml_content=toml_content,
                status="ready",
            )
            db.add(job)
            db.flush()
            db.refresh(job)
            return job.id

    # ------------------------------------------------------------------
    # Retrieve (with stdout fallback)
    # ------------------------------------------------------------------

    def get_blueprint(self, terminal_id: str, terminal_history: Optional[str] = None) -> Optional[dict]:
        """Return serialized blueprint dict, or None if not found.

        If no MCP submission exists and terminal_history is provided, attempts
        to extract a TOML fence block from stdout (non-MCP provider fallback).
        """
        with session_scope() as db:
            job = db.execute(
                select(BlueprintJob).where(BlueprintJob.terminal_id == terminal_id)
            ).scalar_one_or_none()
            if job is not None:
                return self._serialize(job)

        # No MCP submission — try stdout fallback
        if terminal_history:
            toml_content = _extract_toml_from_history(terminal_history)
            if toml_content:
                try:
                    meta, _ = self.parse_toml(toml_content)
                    project_id = meta.get("project_id", "default")
                    self.save_blueprint(terminal_id, project_id, toml_content)
                except BlueprintParseError as exc:
                    return {"error": f"stdout TOML extraction failed: {exc}"}

                with session_scope() as db:
                    job = db.execute(
                        select(BlueprintJob).where(BlueprintJob.terminal_id == terminal_id)
                    ).scalar_one_or_none()
                    if job is not None:
                        return self._serialize(job)

        return None

    def list_blueprints(self, project_id: str, *, limit: int = 20) -> list[dict]:
        """Return recent blueprint jobs for a project, newest first."""
        limit = max(1, min(limit, 100))
        with session_scope() as db:
            jobs = db.execute(
                select(BlueprintJob)
                .where(BlueprintJob.project_id == project_id)
                .order_by(BlueprintJob.created_at.desc(), BlueprintJob.id.desc())
                .limit(limit)
            ).scalars().all()
            results = []
            for job in jobs:
                try:
                    results.append(self._serialize(job, include_toml=True))
                except Exception:
                    LOG.warning("Skipping blueprint job %s - serialization failed", job.id)
        seen_terminal_ids = {item["terminal_id"] for item in results}
        blueprint_dir = Path(constants.BLUEPRINTS_DIR) / project_id
        if blueprint_dir.exists():
            for path in blueprint_dir.glob("*.toml"):
                if path.stem in seen_terminal_ids:
                    continue
                try:
                    results.append(self._serialize_file(path, project_id))
                except Exception:
                    LOG.warning("Skipping blueprint file %s - serialization failed", path)

        return sorted(
            results,
            key=lambda item: (str(item.get("created_at", "")), str(item.get("terminal_id", ""))),
            reverse=True,
        )[:limit]

    def list_project_ids(self) -> list[str]:
        """Return project IDs that have generated blueprint history."""
        project_ids: set[str] = set()
        with session_scope() as db:
            rows = db.execute(select(distinct(BlueprintJob.project_id))).scalars().all()
            project_ids.update(row for row in rows if row)

        root = Path(constants.BLUEPRINTS_DIR)
        if root.exists():
            project_ids.update(path.name for path in root.iterdir() if path.is_dir())

        return sorted(project_ids)

    def _serialize(self, job: BlueprintJob, *, include_toml: bool = False) -> dict:
        meta, items = self.parse_toml(job.toml_content)
        payload = {
            "terminal_id": job.terminal_id,
            "project_id": job.project_id,
            "status": job.status,
            "created_at": str(job.created_at),
            "imported_at": job.imported_at,
            "meta": meta,
            "items": [vars(i) for i in items],
        }
        if include_toml:
            payload["toml_content"] = job.toml_content
        return payload

    def _serialize_file(self, path: Path, project_id: str) -> dict:
        toml_content = path.read_text(encoding="utf-8")
        meta, items = self.parse_toml(toml_content)
        return {
            "terminal_id": path.stem,
            "project_id": meta.get("project_id") or project_id,
            "status": "ready",
            "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            "imported_at": None,
            "meta": meta,
            "items": [vars(i) for i in items],
            "toml_content": toml_content,
        }

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def import_blueprint(
        self,
        terminal_id: str,
        selected_slugs: Optional[list[str]],
        project_id: str,
    ) -> list[WorkItemResponse]:
        """Create WorkItems + WorkEdges for selected slugs. Returns created items."""
        with session_scope() as db:
            job = db.execute(
                select(BlueprintJob).where(BlueprintJob.terminal_id == terminal_id)
            ).scalar_one_or_none()
            if job is None:
                raise ValueError(f"No blueprint found for terminal {terminal_id!r}")
            toml_content = job.toml_content
            job_id = job.id

        _, all_items = self.parse_toml(toml_content)
        items_to_import = (
            [i for i in all_items if i.id in set(selected_slugs)]
            if selected_slugs is not None
            else all_items
        )

        selected_set = {i.id for i in items_to_import}
        slug_to_work_id: dict[str, str] = {}
        created: list[WorkItemResponse] = []

        # Pass 1: create work items
        for item in items_to_import:
            desc = item.description
            if item.effort_hours:
                desc += f"\n\nEffort estimate: {item.effort_hours}h"

            type_val = item.category if item.category in {e.value for e in WorkItemType} else "feature"
            proof_reqs = (
                [ProofType(p) for p in item.proof_requirements if p in {e.value for e in ProofType}]
                if item.proof_requirements
                else None
            )

            wi = self._work.create_work_item(WorkItemCreateRequest(
                project_id=project_id,
                title=item.title,
                description=desc,
                type=WorkItemType(type_val),
                priority=max(1, min(5, item.priority)),
                acceptance_criteria=item.acceptance_criteria,
                files_of_interest=item.files_of_interest,
                proof_requirements=proof_reqs,
                complexity=_effort_to_complexity(item.effort_hours),
            ))
            slug_to_work_id[item.id] = wi.id
            created.append(wi)

        # Pass 2: create dependency edges
        for item in items_to_import:
            target_id = slug_to_work_id[item.id]
            for dep_slug in item.depends_on:
                if dep_slug not in selected_set:
                    continue
                self._work.create_edge(WorkEdgeCreateRequest(
                    from_id=slug_to_work_id[dep_slug],
                    to_id=target_id,
                    type=EdgeType.BLOCKS,
                    created_by="blueprint_import",
                ))

        # Mark job as imported
        with session_scope() as db:
            job = db.get(BlueprintJob, job_id)
            if job:
                job.status = "imported"
                job.imported_at = datetime.utcnow().isoformat()

        LOG.info("Imported %d work items from blueprint for terminal %s", len(created), terminal_id)
        return created


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _extract_toml_from_history(history: str) -> Optional[str]:
    """Extract TOML from terminal stdout.

    Priority order (last match wins to skip example blocks in inbox prompt):
    1. <<<CONDUCTOR_BLUEPRINT_BEGIN/END>>> sentinel — unique, unambiguous
    2. ```toml fenced block — standard markdown
    3. Raw [meta] anchor — last-resort for agents that print bare TOML
    """
    sentinel_matches = list(_SENTINEL_RE.finditer(history))
    if sentinel_matches:
        return sentinel_matches[-1].group(1).strip()

    fence_matches = list(_TOML_FENCE_RE.finditer(history))
    if fence_matches:
        return fence_matches[-1].group(1).strip()

    # rfind skips the example [meta] block that appears earlier in the inbox prompt template
    idx = history.rfind("[meta]")
    if idx != -1:
        return history[idx:].strip()
    return None


def _effort_to_complexity(hours: float) -> int:
    if hours <= 2:
        return 1
    if hours <= 4:
        return 2
    if hours <= 8:
        return 3
    if hours <= 16:
        return 4
    return 5
