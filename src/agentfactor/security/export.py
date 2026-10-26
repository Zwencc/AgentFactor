"""Minimized, idempotent OTLP/HTTP-compatible security audit export."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx

from agentfactor.clients.database import (
    SecurityActionDecision,
    SecurityAuditExport,
    SecurityResponseAction,
    SecurityRiskAlert,
    session_scope,
)


class SecurityExportError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class SecurityAuditExporter:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=15)

    def export(self, limit: int = 200) -> dict[str, Any]:
        endpoint = os.getenv("AGENTFACTOR_SIEM_OTLP_ENDPOINT", "")
        parts = urlsplit(endpoint)
        if parts.scheme != "https" or not parts.hostname:
            raise SecurityExportError("siem_endpoint_invalid", 503)
        destination_digest = _digest(endpoint)
        records = self._records(destination_digest, limit)
        if not records:
            return {"exported": 0, "status": "idle"}
        canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False)
        batch_digest = _digest(canonical)
        payload = {
            "resourceLogs": [{
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "agentfactor"}}]},
                "scopeLogs": [{
                    "scope": {"name": "agentfactor.security"},
                    "logRecords": records,
                }],
            }],
        }
        headers = {
            "content-type": "application/json",
            "idempotency-key": batch_digest,
        }
        token = os.getenv("AGENTFACTOR_SIEM_OTLP_TOKEN")
        if token:
            headers["authorization"] = f"Bearer {token}"
        try:
            response = self.client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SecurityExportError("siem_export_failed", 502) from exc
        with session_scope() as db:
            for record in records:
                db.add(SecurityAuditExport(
                    id=f"audit_export_{uuid.uuid4().hex}",
                    destination_digest=destination_digest,
                    event_id=record["attributes"][0]["value"]["stringValue"],
                    batch_digest=batch_digest,
                ))
        return {"exported": len(records), "status": "exported", "batch_digest": batch_digest}

    @staticmethod
    def _records(destination_digest: str, limit: int) -> list[dict[str, Any]]:
        with session_scope() as db:
            sent = {value.event_id for value in db.query(SecurityAuditExport).filter(
                SecurityAuditExport.destination_digest == destination_digest
            ).all()}
            candidates: list[tuple[str, str, dict[str, str]]] = []
            for value in db.query(SecurityActionDecision).order_by(SecurityActionDecision.created_at).all():
                candidates.append((
                    f"decision:{value.id}",
                    "security.action.decision",
                    {
                        "effect": value.effect,
                        "reason_code": value.reason_code,
                        "surface": value.surface,
                        "policy_version": value.policy_version,
                    },
                ))
            for value in db.query(SecurityRiskAlert).order_by(SecurityRiskAlert.created_at).all():
                candidates.append((
                    f"alert:{value.id}",
                    "security.risk.alert",
                    {"effect": value.effect, "reason_code": value.reason_code},
                ))
            for value in db.query(SecurityResponseAction).order_by(SecurityResponseAction.created_at).all():
                candidates.append((
                    f"response:{value.id}",
                    "security.response.action",
                    {
                        "action": value.action,
                        "target_type": value.target_type,
                        "resulting_status": value.resulting_status,
                    },
                ))
        records = []
        for event_id, event_name, fields in candidates:
            if event_id in sent:
                continue
            attributes = [{"key": "agentfactor.event_id", "value": {"stringValue": event_id}}]
            attributes.extend(
                {"key": f"agentfactor.security.{key}", "value": {"stringValue": value}}
                for key, value in sorted(fields.items())
            )
            records.append({"severityText": "INFO", "body": {"stringValue": event_name}, "attributes": attributes})
            if len(records) >= limit:
                break
        return records
