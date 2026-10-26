"""Immutable policy revisions and fail-closed local bundle publication."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentfactor.clients.database import (
    SecurityPolicy,
    SecurityPolicyBundle,
    SecurityPolicyRollout,
    SecurityPolicyRolloutTarget,
    SecurityPolicyRevision,
    SecurityEndpoint,
    session_scope,
)
from agentfactor.security.gateway import SAFE_ID, SecurityGatewayError, SecurityGatewayService
from agentfactor.security.schemas import SecurityPolicyCreateRequest, SecurityPolicyRevisionCreateRequest


POLICY_COMPILER_VERSION = "agentfactor.policy-compiler/v1alpha1"


class SecurityPolicyError(RuntimeError):
    """A policy operation could not be completed safely."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SecurityPolicyError("policy_document_invalid") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class SecurityPolicyService:
    def __init__(self, config_path: Path | None = None) -> None:
        self.gateway = SecurityGatewayService(config_path=config_path)

    def create_policy(self, request: SecurityPolicyCreateRequest) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(request.id) or not request.display_name.strip() or len(request.display_name) > 128:
            raise SecurityPolicyError("policy_identity_invalid")
        with session_scope() as db:
            if db.get(SecurityPolicy, request.id) is not None:
                raise SecurityPolicyError("policy_already_exists", 409)
            value = SecurityPolicy(id=request.id, display_name=request.display_name.strip())
            db.add(value)
            db.flush()
            return self._policy_dict(value)

    def policies(self) -> list[dict[str, Any]]:
        with session_scope() as db:
            values = db.query(SecurityPolicy).order_by(SecurityPolicy.id).all()
            return [self._policy_dict(value) for value in values]

    def revisions(self, policy_id: str) -> list[dict[str, Any]]:
        with session_scope() as db:
            if db.get(SecurityPolicy, policy_id) is None:
                raise SecurityPolicyError("policy_not_found", 404)
            values = db.query(SecurityPolicyRevision).filter(
                SecurityPolicyRevision.policy_id == policy_id
            ).order_by(SecurityPolicyRevision.version.desc()).all()
            return [self._revision_dict(value) for value in values]

    def create_revision(
        self,
        policy_id: str,
        request: SecurityPolicyRevisionCreateRequest,
    ) -> dict[str, Any]:
        with session_scope() as db:
            policy = db.get(SecurityPolicy, policy_id)
            if policy is None:
                raise SecurityPolicyError("policy_not_found", 404)
            latest = db.query(SecurityPolicyRevision).filter(
                SecurityPolicyRevision.policy_id == policy_id
            ).order_by(SecurityPolicyRevision.version.desc()).first()
            version = 1 if latest is None else latest.version + 1
            raw = {
                "policy_version": f"{policy_id}.v{version}",
                "default_effect": request.default_effect,
                "rules": request.rules,
                "mcp_servers": request.mcp_servers,
            }
            try:
                normalized = self.gateway.normalize_config(raw)
            except SecurityGatewayError as exc:
                raise SecurityPolicyError(exc.code, exc.status_code) from exc
            normalized.pop("policy_digest")
            document_value = {
                **normalized,
                "data_rules": [rule.model_dump(mode="json") for rule in request.data_rules],
                "egress_rules": [rule.model_dump(mode="json") for rule in request.egress_rules],
                "declassification_rules": [
                    rule.model_dump(mode="json") for rule in request.declassification_rules
                ],
                "network_routes": [rule.model_dump(mode="json") for rule in request.network_routes],
                "credentials": [rule.model_dump(mode="json") for rule in request.credentials],
                "model_providers": [rule.model_dump(mode="json") for rule in request.model_providers],
            }
            document = _canonical(document_value)
            value = SecurityPolicyRevision(
                id=f"policy_revision_{uuid.uuid4().hex}",
                policy_id=policy_id,
                version=version,
                document=document,
                document_digest=_digest(document_value),
                status="draft",
            )
            db.add(value)
            db.flush()
            return self._revision_dict(value)

    def publish(self, policy_id: str, revision_id: str) -> dict[str, Any]:
        with session_scope() as db:
            policy = db.get(SecurityPolicy, policy_id)
            revision = db.get(SecurityPolicyRevision, revision_id)
            if policy is None or revision is None or revision.policy_id != policy_id:
                raise SecurityPolicyError("policy_revision_not_found", 404)
            existing = db.query(SecurityPolicyBundle).filter(
                SecurityPolicyBundle.revision_id == revision_id,
                SecurityPolicyBundle.status == "published",
            ).first()
            if existing is not None and policy.active_revision_id == revision_id:
                return self._bundle_dict(existing)
            document = json.loads(revision.document)
            gateway_document = {
                key: document[key]
                for key in ("policy_version", "default_effect", "rules", "mcp_servers")
            }
            bundle_digest = _digest({
                "compiler_version": POLICY_COMPILER_VERSION,
                "policy_document": document,
                "acr_delivery": "NOT_CONFIGURED",
            })
            published_at = datetime.now(timezone.utc)
            signing_key = os.getenv("AGENTFACTOR_POLICY_SIGNING_KEY")
            signing_key_id = os.getenv("AGENTFACTOR_POLICY_SIGNING_KEY_ID", "environment") if signing_key else None
            signature = (
                "hmac-sha256:" + hmac.new(
                    signing_key.encode("utf-8"), bundle_digest.encode("utf-8"), hashlib.sha256
                ).hexdigest()
                if signing_key else None
            )
            bundle = SecurityPolicyBundle(
                id=f"policy_bundle_{uuid.uuid4().hex}",
                revision_id=revision.id,
                compiler_version=POLICY_COMPILER_VERSION,
                bundle_digest=bundle_digest,
                gateway_config=_canonical(gateway_document),
                acr_delivery="NOT_CONFIGURED",
                signature=signature,
                signing_key_id=signing_key_id,
                status="staged",
                published_at=published_at,
            )
            db.add(bundle)
            db.flush()
            bundle_id = bundle.id

        self._write_gateway_config(gateway_document)

        with session_scope() as db:
            policy = db.get(SecurityPolicy, policy_id)
            revision = db.get(SecurityPolicyRevision, revision_id)
            bundle = db.get(SecurityPolicyBundle, bundle_id)
            if policy is None or revision is None or bundle is None:
                raise SecurityPolicyError("policy_publish_state_lost", 500)
            previous = db.query(SecurityPolicyBundle).filter(
                SecurityPolicyBundle.id != bundle.id,
                SecurityPolicyBundle.status == "published",
            ).all()
            for item in previous:
                item.status = "superseded"
            other_policies = db.query(SecurityPolicy).filter(SecurityPolicy.id != policy_id).all()
            for item in other_policies:
                if item.status == "active":
                    item.status = "inactive"
            bundle.status = "published"
            revision.status = "published"
            revision.published_at = bundle.published_at
            policy.status = "active"
            policy.active_revision_id = revision.id
            db.flush()
            return self._bundle_dict(bundle)

    def create_rollout(
        self,
        policy_id: str,
        revision_id: str,
        percentage: int,
        actor_id: str,
    ) -> dict[str, Any]:
        with session_scope() as db:
            revision = db.get(SecurityPolicyRevision, revision_id)
            bundle = db.query(SecurityPolicyBundle).filter(
                SecurityPolicyBundle.revision_id == revision_id,
                SecurityPolicyBundle.status == "published",
            ).first()
            if revision is None or revision.policy_id != policy_id or bundle is None:
                raise SecurityPolicyError("published_policy_bundle_not_found", 404)
            if bundle.signature is None:
                raise SecurityPolicyError("signed_policy_bundle_required", 409)
            rollout = SecurityPolicyRollout(
                id=f"policy_rollout_{uuid.uuid4().hex}",
                bundle_id=bundle.id,
                percentage=percentage,
                status="planned",
                created_by=actor_id,
            )
            db.add(rollout)
            selected = []
            endpoints = db.query(SecurityEndpoint).order_by(SecurityEndpoint.id).all()
            for endpoint in endpoints:
                cohort = int(hashlib.sha256(
                    f"{bundle.bundle_digest}:{endpoint.id}".encode("utf-8")
                ).hexdigest()[:8], 16) % 100
                if cohort >= percentage:
                    continue
                db.add(SecurityPolicyRolloutTarget(
                    id=f"policy_rollout_target_{uuid.uuid4().hex}",
                    rollout_id=rollout.id,
                    endpoint_id=endpoint.id,
                    cohort=cohort,
                    status="pending",
                ))
                selected.append({"endpoint_id": endpoint.id, "cohort": cohort, "status": "pending"})
            return {
                "id": rollout.id,
                "bundle_id": bundle.id,
                "percentage": percentage,
                "status": "planned",
                "targets": selected,
            }

    def verify_active_gateway(self, policy_id: str) -> dict[str, Any]:
        with session_scope() as db:
            policy = db.get(SecurityPolicy, policy_id)
            if policy is None or policy.active_revision_id is None:
                raise SecurityPolicyError("active_policy_not_found", 404)
            bundle = db.query(SecurityPolicyBundle).filter(
                SecurityPolicyBundle.revision_id == policy.active_revision_id,
                SecurityPolicyBundle.status == "published",
            ).first()
            if bundle is None:
                raise SecurityPolicyError("published_policy_bundle_not_found", 404)
            expected = bundle.gateway_config
        try:
            raw = json.loads(self.gateway.config_path.read_text(encoding="utf-8"))
            normalized = self.gateway.normalize_config(raw)
            normalized.pop("policy_digest")
            actual = _canonical(normalized)
        except (OSError, json.JSONDecodeError, SecurityGatewayError):
            actual = ""
        return {
            "policy_id": policy_id,
            "status": "healthy" if hmac.compare_digest(expected, actual) else "drifted",
            "expected_digest": _digest(expected),
            "actual_digest": _digest(actual),
        }

    def _write_gateway_config(self, document: dict[str, Any]) -> None:
        path = self.gateway.config_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(_canonical(document))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_name, 0o600)
                os.replace(temporary_name, path)
            except Exception:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
        except OSError as exc:
            raise SecurityPolicyError("policy_publish_failed", 503) from exc

    @staticmethod
    def _policy_dict(value: SecurityPolicy) -> dict[str, Any]:
        return {
            "id": value.id,
            "display_name": value.display_name,
            "status": value.status,
            "active_revision_id": value.active_revision_id,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
        }

    @staticmethod
    def _revision_dict(value: SecurityPolicyRevision) -> dict[str, Any]:
        return {
            "id": value.id,
            "policy_id": value.policy_id,
            "version": value.version,
            "document_digest": value.document_digest,
            "status": value.status,
            "created_at": value.created_at,
            "published_at": value.published_at,
        }

    @staticmethod
    def _bundle_dict(value: SecurityPolicyBundle) -> dict[str, Any]:
        return {
            "id": value.id,
            "revision_id": value.revision_id,
            "compiler_version": value.compiler_version,
            "bundle_digest": value.bundle_digest,
            "acr_delivery": value.acr_delivery,
            "signature": value.signature,
            "signing_key_id": value.signing_key_id,
            "status": value.status,
            "published_at": value.published_at,
        }
