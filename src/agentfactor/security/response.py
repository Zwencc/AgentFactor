"""Reversible, evidence-backed security response operations."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from agentfactor.clients.database import (
    SecurityEndpoint,
    SecurityNativeSession,
    SecurityResponseAction,
    session_scope,
)


class SecurityResponseError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class SecurityResponseService:
    def session_status(self, session_id: str, blocked: bool, actor_id: str, reason: str) -> dict:
        with session_scope() as db:
            session = db.get(SecurityNativeSession, session_id)
            if session is None:
                raise SecurityResponseError("native_session_not_found", 404)
            previous = session.status
            if blocked:
                resulting = "blocked"
                action = "session_block"
            else:
                expires = session.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= datetime.now(timezone.utc):
                    raise SecurityResponseError("native_session_expired", 409)
                resulting = "active"
                action = "session_unblock"
            session.status = resulting
            return self._record(db, action, "native_session", session_id, actor_id, reason, previous, resulting)

    def endpoint_status(self, endpoint_id: str, quarantined: bool, actor_id: str, reason: str) -> dict:
        with session_scope() as db:
            endpoint = db.get(SecurityEndpoint, endpoint_id)
            if endpoint is None:
                raise SecurityResponseError("security_endpoint_not_found", 404)
            previous = endpoint.status
            resulting = "quarantined" if quarantined else "unknown"
            action = "endpoint_quarantine" if quarantined else "endpoint_release"
            endpoint.status = resulting
            return self._record(db, action, "endpoint", endpoint_id, actor_id, reason, previous, resulting)

    @staticmethod
    def _record(db, action: str, target_type: str, target_id: str, actor_id: str, reason: str, previous: str, resulting: str) -> dict:
        value = SecurityResponseAction(
            id=f"response_action_{uuid.uuid4().hex}",
            action=action,
            target_type=target_type,
            target_id=target_id,
            actor_id=actor_id,
            reason_digest=_digest(reason),
            previous_status=previous,
            resulting_status=resulting,
        )
        db.add(value)
        return {
            "id": value.id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "previous_status": previous,
            "resulting_status": resulting,
        }
