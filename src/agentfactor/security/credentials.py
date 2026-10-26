"""Server-side credential checkout that never returns long-term secrets to agents."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from agentfactor.clients.database import SecurityCredentialLease, session_scope
from agentfactor.security.dataflow import DataFlowService


ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,127}")


class CredentialBrokerError(RuntimeError):
    def __init__(self, code: str, status_code: int = 403) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class CredentialBroker:
    def __init__(self, dataflow: DataFlowService | None = None) -> None:
        self.dataflow = dataflow or DataFlowService()

    def checkout(self, credential_id: str, session_id: str, surface: str, resource: str) -> tuple[str, str]:
        document, _policy_digest = self.dataflow.active_document()
        rule = next((item for item in document.get("credentials", []) if item.get("id") == credential_id), None)
        if (
            rule is None
            or surface not in rule.get("allowed_surfaces", [])
            or not any(resource.startswith(prefix) for prefix in rule.get("resource_prefixes", []))
        ):
            raise CredentialBrokerError("credential_scope_blocked")
        source_env = rule.get("source_env", "")
        if not ENV_NAME.fullmatch(source_env):
            raise CredentialBrokerError("credential_source_invalid", 503)
        secret = os.getenv(source_env)
        if not secret:
            raise CredentialBrokerError("credential_unavailable", 503)
        now = datetime.now(timezone.utc)
        lease_id = f"credential_lease_{uuid.uuid4().hex}"
        with session_scope() as db:
            db.add(SecurityCredentialLease(
                id=lease_id,
                credential_id=credential_id,
                session_id=session_id,
                surface=surface,
                resource_digest=_digest(resource),
                secret_digest=_digest(secret),
                status="consumed",
                expires_at=now + timedelta(seconds=rule.get("ttl_seconds", 60)),
                consumed_at=now,
            ))
        return secret, lease_id
