"""Deterministic data labels, session taint, egress checks, and declassification evidence."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

from agentfactor.clients.database import (
    SecurityDeclassification,
    SecurityNativeSession,
    SecurityPolicy,
    SecurityPolicyRevision,
    session_scope,
)


class DataFlowError(RuntimeError):
    """A data-flow policy rejected an operation."""

    def __init__(self, code: str, status_code: int = 403) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class DataFlowService:
    def active_document(self) -> tuple[dict[str, Any], str]:
        with session_scope() as db:
            policy = db.query(SecurityPolicy).filter(SecurityPolicy.status == "active").first()
            if policy is None or policy.active_revision_id is None:
                return {}, "sha256:none"
            revision = db.get(SecurityPolicyRevision, policy.active_revision_id)
            if revision is None or revision.status != "published":
                raise DataFlowError("active_policy_revision_missing", 503)
            try:
                return json.loads(revision.document), revision.document_digest
            except json.JSONDecodeError as exc:
                raise DataFlowError("active_policy_document_invalid", 503) from exc

    def labels_for_path(self, path: str) -> set[str]:
        document, _digest_value = self.active_document()
        resource = os.path.realpath(path)
        labels: set[str] = set()
        for rule in document.get("data_rules", []):
            if any(self._path_matches(resource, prefix) for prefix in rule.get("resource_prefixes", [])):
                labels.update(rule.get("labels", []))
        return labels

    def labels_for_scope(self, path: str) -> set[str]:
        document, _digest_value = self.active_document()
        scope = os.path.realpath(path)
        labels: set[str] = set()
        for rule in document.get("data_rules", []):
            for prefix in rule.get("resource_prefixes", []):
                if self._path_matches(scope, prefix) or self._path_matches(os.path.realpath(prefix), scope):
                    labels.update(rule.get("labels", []))
                    break
        return labels

    def taint_session(self, session_id: str, labels: set[str]) -> list[str]:
        with session_scope() as db:
            session = db.get(SecurityNativeSession, session_id)
            if session is None:
                raise DataFlowError("native_session_not_found", 404)
            current = set(json.loads(session.taint_labels or "[]"))
            current.update(labels)
            session.taint_labels = json.dumps(sorted(current), separators=(",", ":"))
            return sorted(current)

    def session_labels(self, session_id: str) -> list[str]:
        with session_scope() as db:
            session = db.get(SecurityNativeSession, session_id)
            if session is None:
                raise DataFlowError("native_session_not_found", 404)
            return sorted(set(json.loads(session.taint_labels or "[]")))

    def authorize_egress(self, session_id: str, surface: str, destination: str) -> dict[str, Any]:
        labels = set(self.session_labels(session_id))
        if not labels or labels == {"public"}:
            return {"labels": sorted(labels), "rule_ids": []}
        document, policy_digest = self.active_document()
        allowed = {"public"}
        rule_ids: list[str] = []
        for rule in document.get("egress_rules", []):
            if surface not in rule.get("surfaces", []):
                continue
            if not any(destination.startswith(prefix) for prefix in rule.get("destination_prefixes", [])):
                continue
            allowed.update(rule.get("allowed_labels", []))
            rule_ids.append(rule.get("id", ""))
        denied = sorted(labels - allowed)
        if denied:
            raise DataFlowError("sensitive_egress_blocked")
        return {
            "labels": sorted(labels),
            "rule_ids": sorted(rule_id for rule_id in rule_ids if rule_id),
            "policy_digest": policy_digest,
        }

    def declassify(self, session_id: str, rule_id: str, purpose: str) -> dict[str, Any]:
        document, policy_digest = self.active_document()
        rule = next(
            (item for item in document.get("declassification_rules", []) if item.get("id") == rule_id),
            None,
        )
        if rule is None or rule.get("purpose") != purpose:
            raise DataFlowError("declassification_not_authorized")
        remove = set(rule.get("remove_labels", []))
        with session_scope() as db:
            session = db.get(SecurityNativeSession, session_id)
            if session is None:
                raise DataFlowError("native_session_not_found", 404)
            current = set(json.loads(session.taint_labels or "[]"))
            removed = sorted(current & remove)
            remaining = sorted(current - remove)
            session.taint_labels = json.dumps(remaining, separators=(",", ":"))
            db.add(SecurityDeclassification(
                id=f"declassification_{uuid.uuid4().hex}",
                session_id=session_id,
                rule_id=rule_id,
                purpose_digest=_digest(purpose),
                removed_labels=json.dumps(removed, separators=(",", ":")),
                policy_digest=policy_digest,
            ))
        return {"removed_labels": removed, "remaining_labels": remaining, "policy_digest": policy_digest}

    @staticmethod
    def _path_matches(resource: str, prefix: str) -> bool:
        if not os.path.isabs(prefix):
            return False
        normalized = os.path.realpath(prefix).rstrip(os.sep) or os.sep
        if normalized == os.sep:
            return resource.startswith(os.sep)
        return resource == normalized or resource.startswith(normalized + os.sep)
