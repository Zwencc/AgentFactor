"""LLM-backed semantic review and verifier run persistence."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy import desc, select

from agentfactor.clients.database import (
    LLMProviderConfig,
    TerminalAnalysis,
    VerifierCheck,
    VerifierRun,
    WorkItem,
    session_scope,
)
from agentfactor.models.review import LLMProviderCreateRequest, LLMProviderUpdateRequest

LOG = logging.getLogger(__name__)

DEFAULT_REVIEW_THRESHOLD = 75.0


def _json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


class LLMReviewService:
    """Manage review providers and run semantic verifier checks."""

    # ------------------------------------------------------------------
    # Provider configuration
    # ------------------------------------------------------------------

    def list_providers(self) -> list[dict[str, Any]]:
        with session_scope() as db:
            rows = db.execute(
                select(LLMProviderConfig).order_by(LLMProviderConfig.id)
            ).scalars().all()
            return [self._provider_to_dict(row) for row in rows]

    def get_provider(self, provider_id: int) -> Optional[dict[str, Any]]:
        with session_scope() as db:
            row = db.get(LLMProviderConfig, provider_id)
            return self._provider_to_dict(row) if row else None

    def create_provider(self, req: LLMProviderCreateRequest) -> dict[str, Any]:
        with session_scope() as db:
            should_activate = req.is_active or not db.execute(
                select(LLMProviderConfig.id).limit(1)
            ).first()
            if should_activate:
                self._deactivate_all(db)
            row = LLMProviderConfig(
                name=req.name,
                provider_type=req.provider_type,
                base_url=req.base_url,
                api_key=req.api_key,
                model=req.model,
                is_active=should_activate,
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return self._provider_to_dict(row)

    def update_provider(
        self,
        provider_id: int,
        req: LLMProviderUpdateRequest,
    ) -> Optional[dict[str, Any]]:
        with session_scope() as db:
            row = db.get(LLMProviderConfig, provider_id)
            if row is None:
                return None
            if req.is_active:
                self._deactivate_all(db)
            for field in ("name", "provider_type", "base_url", "api_key", "model", "is_active"):
                value = getattr(req, field)
                if value is not None:
                    setattr(row, field, value)
            row.updated_at = datetime.utcnow()
            db.flush()
            db.refresh(row)
            return self._provider_to_dict(row)

    def delete_provider(self, provider_id: int) -> bool:
        with session_scope() as db:
            row = db.get(LLMProviderConfig, provider_id)
            if row is None:
                return False
            was_active = row.is_active
            db.delete(row)
            db.flush()
            if was_active:
                replacement = db.execute(
                    select(LLMProviderConfig).order_by(LLMProviderConfig.id).limit(1)
                ).scalar_one_or_none()
                if replacement is not None:
                    replacement.is_active = True
                    replacement.updated_at = datetime.utcnow()
            return True

    def activate_provider(self, provider_id: int) -> Optional[dict[str, Any]]:
        with session_scope() as db:
            row = db.get(LLMProviderConfig, provider_id)
            if row is None:
                return None
            self._deactivate_all(db)
            row.is_active = True
            row.updated_at = datetime.utcnow()
            db.flush()
            db.refresh(row)
            return self._provider_to_dict(row)

    def test_provider(self, provider_id: int, prompt: str = "hello") -> dict[str, Any]:
        with session_scope() as db:
            provider = db.get(LLMProviderConfig, provider_id)
            if provider is None:
                return {"ok": False, "error": "provider_not_found"}
            config = self._provider_config_snapshot(provider)
        try:
            self._call_provider(prompt, config)
            return {"ok": True, "error": None}
        except Exception as exc:
            LOG.debug("Provider test failed for %s", provider_id, exc_info=True)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Review execution
    # ------------------------------------------------------------------

    def review_terminal(
        self,
        terminal_id: str,
        *,
        force: bool = False,
        trigger_source: str = "manual",
        threshold: float = DEFAULT_REVIEW_THRESHOLD,
    ) -> Optional[dict[str, Any]]:
        """Run or return the semantic review for a terminal analysis."""
        with session_scope() as db:
            analysis = db.execute(
                select(TerminalAnalysis).where(TerminalAnalysis.terminal_id == terminal_id)
            ).scalar_one_or_none()
            if analysis is None:
                return None

            existing_review = _json_loads(analysis.llm_review, None)
            if analysis.review_status == "done" and existing_review is not None and not force:
                run = self._latest_run_for_analysis(db, analysis.id)
                checks = self._checks_for_run(db, run.id) if run else []
                return self._terminal_review_payload(analysis, existing_review, run, checks)

            if not analysis.work_item_id:
                analysis.review_status = "skipped"
                analysis.review_error = "terminal_has_no_work_item"
                analysis.reviewed_at = datetime.utcnow()
                return self._terminal_review_payload(analysis, None, None, [])

            work_item = db.get(WorkItem, analysis.work_item_id)
            if work_item is None:
                analysis.review_status = "skipped"
                analysis.review_error = "work_item_not_found"
                analysis.reviewed_at = datetime.utcnow()
                return self._terminal_review_payload(analysis, None, None, [])

            provider = db.execute(
                select(LLMProviderConfig)
                .where(LLMProviderConfig.is_active == True)  # noqa: E712
                .order_by(LLMProviderConfig.id)
                .limit(1)
            ).scalar_one_or_none()
            if provider is None:
                run = self._create_run(
                    db,
                    work_item_id=work_item.id,
                    terminal_id=analysis.terminal_id,
                    analysis_id=analysis.id,
                    trigger_source=trigger_source,
                    status="skipped",
                    threshold=threshold,
                    failure_reason="no_active_llm_provider",
                )
                analysis.review_status = "skipped"
                analysis.review_error = "no_active_llm_provider"
                analysis.reviewed_at = datetime.utcnow()
                checks = self._checks_for_run(db, run.id)
                return self._terminal_review_payload(analysis, None, run, checks)

            config = self._provider_config_snapshot(provider)
            run = self._create_run(
                db,
                work_item_id=work_item.id,
                terminal_id=analysis.terminal_id,
                analysis_id=analysis.id,
                trigger_source=trigger_source,
                status="running",
                threshold=threshold,
            )
            analysis.review_status = "running"
            analysis.review_error = None
            analysis.review_provider_id = provider.id
            analysis.review_model = provider.model
            db.flush()

            prompt = self._build_prompt(work_item, analysis)
            run_id = run.id
            analysis_id = analysis.id

        try:
            raw = self._call_provider(prompt, config)
            review = self._parse_response(raw)
            score = float(review.get("compliance_score") or 0)
            verdict = str(review.get("verdict", "")).lower()
            passed = score >= threshold and verdict not in {"fail", "failed", "missed"}
            status = "pass" if passed else "fail"
            summary = (
                review.get("deviation_summary")
                or review.get("reviewer_notes")
                or f"LLM review score: {score:g}"
            )
            failure_reason = None if passed else summary

            with session_scope() as db:
                run = db.get(VerifierRun, run_id)
                analysis = db.get(TerminalAnalysis, analysis_id)
                if run is None or analysis is None:
                    raise RuntimeError("review row disappeared while running")
                run.status = status
                run.summary = str(summary)[:4000] if summary else None
                run.failure_reason = str(failure_reason)[:4000] if failure_reason else None
                run.finished_at = datetime.utcnow()
                run.raw_artifacts = json.dumps({
                    "provider_id": config["id"],
                    "provider_type": config["provider_type"],
                    "model": config["model"],
                })
                check = VerifierCheck(
                    run_id=run.id,
                    check_type="llm_review",
                    name="semantic_review",
                    status=status,
                    score=score,
                    threshold=threshold,
                    output_excerpt=str(summary)[:2000] if summary else None,
                    artifact_ref=f"terminal_analyses:{analysis.id}:llm_review",
                )
                db.add(check)
                analysis.llm_review = json.dumps(review, ensure_ascii=False)
                analysis.llm_review_raw = raw
                analysis.review_status = "done"
                analysis.review_model = config["model"]
                analysis.review_provider_id = config["id"]
                analysis.review_error = None
                analysis.reviewed_at = datetime.utcnow()
                db.flush()
                db.refresh(run)
                checks = self._checks_for_run(db, run.id)
                return self._terminal_review_payload(analysis, review, run, checks)
        except Exception as exc:
            LOG.warning("LLM review failed for terminal %s", terminal_id, exc_info=True)
            with session_scope() as db:
                run = db.get(VerifierRun, run_id)
                analysis = db.get(TerminalAnalysis, analysis_id)
                message = str(exc)
                if run is not None:
                    run.status = "error"
                    run.failure_reason = message[:4000]
                    run.finished_at = datetime.utcnow()
                    db.add(VerifierCheck(
                        run_id=run.id,
                        check_type="llm_review",
                        name="semantic_review",
                        status="error",
                        threshold=threshold,
                        output_excerpt=message[:2000],
                    ))
                if analysis is not None:
                    analysis.review_status = "error"
                    analysis.review_error = message[:4000]
                    analysis.reviewed_at = datetime.utcnow()
                db.flush()
                checks = self._checks_for_run(db, run.id) if run else []
                if analysis is None:
                    return None
                return self._terminal_review_payload(analysis, None, run, checks)

    def get_terminal_review(self, terminal_id: str) -> Optional[dict[str, Any]]:
        with session_scope() as db:
            analysis = db.execute(
                select(TerminalAnalysis).where(TerminalAnalysis.terminal_id == terminal_id)
            ).scalar_one_or_none()
            if analysis is None:
                return None
            run = self._latest_run_for_analysis(db, analysis.id)
            checks = self._checks_for_run(db, run.id) if run else []
            review = _json_loads(analysis.llm_review, None)
            return self._terminal_review_payload(analysis, review, run, checks)

    def list_verifier_runs(self, work_item_id: str) -> list[dict[str, Any]]:
        with session_scope() as db:
            rows = db.execute(
                select(VerifierRun)
                .where(VerifierRun.work_item_id == work_item_id)
                .order_by(desc(VerifierRun.created_at), desc(VerifierRun.id))
            ).scalars().all()
            return [self._run_to_dict(row, self._checks_for_run(db, row.id)) for row in rows]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _deactivate_all(db) -> None:  # type: ignore[no-untyped-def]
        rows = db.execute(
            select(LLMProviderConfig).where(LLMProviderConfig.is_active == True)  # noqa: E712
        ).scalars().all()
        for row in rows:
            row.is_active = False
            row.updated_at = datetime.utcnow()

    @staticmethod
    def _provider_to_dict(row: LLMProviderConfig) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "provider_type": row.provider_type,
            "base_url": row.base_url,
            "model": row.model,
            "is_active": bool(row.is_active),
            "api_key_set": bool(row.api_key),
            "created_at": str(row.created_at),
            "updated_at": str(row.updated_at),
        }

    @staticmethod
    def _provider_config_snapshot(row: LLMProviderConfig) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "provider_type": row.provider_type,
            "base_url": row.base_url,
            "api_key": row.api_key,
            "model": row.model,
        }

    def _create_run(
        self,
        db,  # type: ignore[no-untyped-def]
        *,
        work_item_id: str,
        terminal_id: Optional[str],
        analysis_id: Optional[int],
        trigger_source: str,
        status: str,
        threshold: float,
        failure_reason: Optional[str] = None,
    ) -> VerifierRun:
        previous = db.execute(
            select(VerifierRun.attempt_no)
            .where(VerifierRun.work_item_id == work_item_id)
            .order_by(desc(VerifierRun.attempt_no))
            .limit(1)
        ).scalar()
        strategy = {
            "required_checks": [{"type": "llm_review", "threshold": threshold}],
            "optional_checks": [],
            "pass_policy": "all_required_pass",
            "max_attempts": 3,
            "feedback_channel": "inbox",
            "allow_llm_to_gate_done": False,
            "count_error_as_failure": False,
        }
        now = datetime.utcnow()
        run = VerifierRun(
            work_item_id=work_item_id,
            terminal_id=terminal_id,
            analysis_id=analysis_id,
            attempt_no=int(previous or 0) + 1,
            trigger_source=trigger_source,
            status=status,
            strategy_json=json.dumps(strategy),
            failure_reason=failure_reason,
            raw_artifacts="{}",
            started_at=now if status == "running" else None,
            finished_at=now if status in {"skipped", "error", "pass", "fail"} else None,
        )
        db.add(run)
        db.flush()
        db.refresh(run)
        if status == "skipped":
            db.add(VerifierCheck(
                run_id=run.id,
                check_type="llm_review",
                name="semantic_review",
                status="skipped",
                threshold=threshold,
                output_excerpt=failure_reason,
            ))
            db.flush()
        return run

    def _build_prompt(self, work_item: WorkItem, analysis: TerminalAnalysis) -> str:
        acceptance = _json_loads(work_item.acceptance_criteria, [])
        files_of_interest = _json_loads(work_item.files_of_interest, [])
        tool_stats = _json_loads(analysis.tool_stats, {})
        files_touched = _json_loads(analysis.files_touched, [])
        commands_run = _json_loads(analysis.commands_run, [])
        risk_flags = _json_loads(analysis.risk_flags, [])
        turns = _json_loads(analysis.conversation_turns, [])
        separator = [{"type": "separator", "content": "..."}] if len(turns) > 10 else []
        sampled_turns = turns[:5] + separator + turns[-5:]
        payload = {
            "work_item": {
                "id": work_item.id,
                "title": work_item.title,
                "description": work_item.description,
                "type": getattr(work_item.type, "value", str(work_item.type)),
                "acceptance_criteria": acceptance,
                "files_of_interest": files_of_interest,
            },
            "observed_behavior": {
                "terminal_id": analysis.terminal_id,
                "provider": analysis.provider,
                "tool_stats": tool_stats,
                "files_touched": files_touched,
                "commands_run": commands_run,
                "risk_flags": risk_flags,
                "line_count": analysis.line_count,
                "conversation_turns_sample": sampled_turns,
            },
        }
        schema = {
            "compliance_score": "number 0-100",
            "verdict": "pass | partial | fail",
            "requirement_checks": [
                {
                    "criterion": "acceptance criterion text",
                    "status": "met | partial | missed",
                    "evidence": "specific observed evidence or absence",
                    "suggestion": "specific improvement",
                }
            ],
            "deviation_summary": "2-3 sentence summary",
            "work_item_improvements": {
                "description": "suggested description text",
                "add_criteria": ["additional acceptance criteria"],
                "clarify": ["ambiguous wording to clarify"],
            },
            "risk_assessment": "low | medium | high",
            "reviewer_notes": "additional notes",
        }
        return (
            "You are an independent engineering verifier. Compare the work item requirements "
            "with the observed terminal behavior. Return strict JSON only, with this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\n"
            "Input:\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    def _call_provider(self, prompt: str, config: dict[str, Any]) -> str:
        if config["provider_type"] == "anthropic":
            return self._call_anthropic(prompt, config)
        return self._call_openai_compatible(prompt, config)

    def _call_openai_compatible(self, prompt: str, config: dict[str, Any]) -> str:
        url = config["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        payload = {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {config['api_key']}"}
        with httpx.Client(timeout=30) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]

    def _call_anthropic(self, prompt: str, config: dict[str, Any]) -> str:
        url = config["base_url"].rstrip("/")
        if not url.endswith("/messages"):
            url = f"{url}/messages"
        payload = {
            "model": config["model"],
            "max_tokens": 2048,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": config["api_key"],
            "anthropic-version": "2023-06-01",
        }
        with httpx.Client(timeout=30) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        parts = data.get("content") or []
        return "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("LLM did not return JSON")
            parsed = json.loads(raw[start:end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("LLM review JSON must be an object")
        parsed.setdefault("compliance_score", 0)
        parsed.setdefault("verdict", "partial")
        parsed.setdefault("requirement_checks", [])
        parsed.setdefault("deviation_summary", "")
        parsed.setdefault(
            "work_item_improvements",
            {"description": "", "add_criteria": [], "clarify": []},
        )
        parsed.setdefault("risk_assessment", "medium")
        parsed.setdefault("reviewer_notes", "")
        return parsed

    @staticmethod
    def _latest_run_for_analysis(db, analysis_id: int) -> Optional[VerifierRun]:  # type: ignore[no-untyped-def]
        return db.execute(
            select(VerifierRun)
            .where(VerifierRun.analysis_id == analysis_id)
            .order_by(desc(VerifierRun.created_at), desc(VerifierRun.id))
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _checks_for_run(db, run_id: int) -> list[VerifierCheck]:  # type: ignore[no-untyped-def]
        return db.execute(
            select(VerifierCheck).where(VerifierCheck.run_id == run_id).order_by(VerifierCheck.id)
        ).scalars().all()

    def _terminal_review_payload(
        self,
        analysis: TerminalAnalysis,
        review: Optional[dict[str, Any]],
        run: Optional[VerifierRun],
        checks: list[VerifierCheck],
    ) -> dict[str, Any]:
        return {
            "terminal_id": analysis.terminal_id,
            "analysis_id": analysis.id,
            "work_item_id": analysis.work_item_id,
            "status": analysis.review_status,
            "review": review,
            "review_error": analysis.review_error,
            "verifier_run": self._run_to_dict(run, checks) if run else None,
        }

    def _run_to_dict(self, row: VerifierRun, checks: list[VerifierCheck]) -> dict[str, Any]:
        return {
            "id": row.id,
            "work_item_id": row.work_item_id,
            "terminal_id": row.terminal_id,
            "analysis_id": row.analysis_id,
            "attempt_no": row.attempt_no,
            "trigger_source": row.trigger_source,
            "status": row.status,
            "strategy": _json_loads(row.strategy_json, {}),
            "summary": row.summary,
            "failure_reason": row.failure_reason,
            "raw_artifacts": _json_loads(row.raw_artifacts, {}),
            "started_at": str(row.started_at) if row.started_at else None,
            "finished_at": str(row.finished_at) if row.finished_at else None,
            "created_at": str(row.created_at),
            "checks": [self._check_to_dict(check) for check in checks],
        }

    @staticmethod
    def _check_to_dict(row: VerifierCheck) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "check_type": row.check_type,
            "name": row.name,
            "status": row.status,
            "command": row.command,
            "exit_code": row.exit_code,
            "score": row.score,
            "threshold": row.threshold,
            "output_excerpt": row.output_excerpt,
            "artifact_ref": row.artifact_ref,
            "created_at": str(row.created_at),
        }
