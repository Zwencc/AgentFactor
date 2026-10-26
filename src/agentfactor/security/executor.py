"""Token-bound Native SDK sessions and controlled local executors."""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agentfactor import constants
from agentfactor.clients.database import SecurityNativeSession, session_scope
from agentfactor.security.dataflow import DataFlowError, DataFlowService
from agentfactor.security.credentials import CredentialBrokerError
from agentfactor.security.gateway import SecurityGatewayError, SecurityGatewayService
from agentfactor.security.network import NetworkEgressError, NetworkEgressService
from agentfactor.security.model import ModelEgressError, ModelEgressService
from agentfactor.security.schemas import (
    CanonicalSecurityAction,
    NativeFilesystemRequest,
    NativeDeclassificationRequest,
    NativeMCPRequest,
    NativeNetworkRequest,
    NativeModelRequest,
    NativeProcessRequest,
    NativeSessionCreateRequest,
    MCPCallRequest,
)


MAXIMUM_NATIVE_IO_BYTES = 1 << 20


class NativeExecutorError(RuntimeError):
    """A Native SDK request could not be executed safely."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _token_hash(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _path_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(str(path).encode("utf-8")).hexdigest()


class NativeExecutorService:
    def __init__(
        self,
        gateway: SecurityGatewayService | None = None,
        dataflow: DataFlowService | None = None,
        network: NetworkEgressService | None = None,
        model: ModelEgressService | None = None,
    ) -> None:
        self.gateway = gateway or SecurityGatewayService()
        self.dataflow = dataflow or DataFlowService()
        self.network = network or NetworkEgressService(self.dataflow)
        self.model = model or ModelEgressService(self.dataflow, self.network)

    def create_session(self, request: NativeSessionCreateRequest) -> dict[str, Any]:
        workspace = Path(request.workspace_path).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise NativeExecutorError("native_workspace_invalid")
        control_root = constants.HOME_DIR.resolve(strict=False)
        if workspace == control_root or control_root in workspace.parents or workspace in control_root.parents:
            raise NativeExecutorError("native_workspace_conflicts_with_control_data")
        token = secrets.token_urlsafe(32)
        session_id = f"native_session_{uuid.uuid4().hex}"
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=request.ttl_seconds)
        with session_scope() as db:
            db.add(SecurityNativeSession(
                id=session_id,
                token_hash=_token_hash(token),
                principal_id=request.principal.id,
                project_id=request.project_id,
                workspace_path=str(workspace),
                workspace_digest=_path_digest(workspace),
                agent_name=request.agent.name,
                agent_version=request.agent.version,
                status="active",
                assurance="OBSERVED",
                expires_at=expires_at,
            ))
        return {
            "id": session_id,
            "token": token,
            "project_id": request.project_id,
            "workspace_digest": _path_digest(workspace),
            "status": "active",
            "assurance": "OBSERVED",
            "expires_at": expires_at,
        }

    def close_session(self, session_id: str, token: str) -> dict[str, Any]:
        session = self._session(session_id, token)
        with session_scope() as db:
            value = db.get(SecurityNativeSession, session.id)
            if value is None:
                raise NativeExecutorError("native_session_not_found", 404)
            value.status = "closed"
            value.ended_at = datetime.now(timezone.utc)
        return {"id": session_id, "status": "closed"}

    def filesystem(self, request: NativeFilesystemRequest) -> dict[str, Any]:
        session = self._session(request.session_id, request.token)
        workspace = Path(session.workspace_path)
        target = self._scoped_path(workspace, request.path)
        if request.operation == "write_text" and request.content is None:
            raise NativeExecutorError("native_filesystem_content_required")
        if request.content is not None and len(request.content.encode("utf-8")) > MAXIMUM_NATIVE_IO_BYTES:
            raise NativeExecutorError("native_io_too_large", 413)
        params = {"content_digest": _token_hash(request.content)} if request.content is not None else {}
        decision, consumed = self._authorize(
            session,
            "filesystem",
            request.operation,
            str(target),
            params,
        )
        started = time.monotonic()
        succeeded = False
        summary: dict[str, Any] = {"operation": request.operation}
        try:
            if request.operation == "read_text":
                if not target.is_file() or target.stat().st_size > MAXIMUM_NATIVE_IO_BYTES:
                    raise NativeExecutorError("native_file_unreadable", 400)
                content = target.read_text(encoding="utf-8")
                summary.update({"bytes": len(content.encode("utf-8")), "content_digest": _token_hash(content)})
                labels = self.dataflow.labels_for_path(str(target))
                session_taint = self.dataflow.taint_session(session.id, labels)
                result: dict[str, Any] = {
                    "content": content,
                    "labels": sorted(labels),
                    "session_taint": session_taint,
                }
            elif request.operation == "write_text":
                target.write_text(request.content or "", encoding="utf-8")
                summary["bytes"] = len((request.content or "").encode("utf-8"))
                result = {"written": True}
            elif request.operation == "mkdir":
                target.mkdir()
                result = {"created": True}
            elif request.operation == "delete_file":
                if not target.is_file():
                    raise NativeExecutorError("native_file_not_found", 404)
                target.unlink()
                result = {"deleted": True}
            else:
                if not target.is_dir():
                    raise NativeExecutorError("native_directory_not_found", 404)
                entries = sorted(path.name for path in target.iterdir())
                summary["entries"] = len(entries)
                result = {"entries": entries}
            succeeded = True
            return self._execution_response(decision, result)
        except NativeExecutorError:
            raise
        except (OSError, UnicodeError) as exc:
            raise NativeExecutorError("native_filesystem_failed", 500) from exc
        finally:
            self.gateway.record_execution_result(
                decision["id"],
                consumed["grant_id"],
                succeeded,
                summary,
                int((time.monotonic() - started) * 1000),
            )

    def process(self, request: NativeProcessRequest) -> dict[str, Any]:
        session = self._session(request.session_id, request.token)
        if any(not item or "\x00" in item or len(item) > 8192 for item in request.argv):
            raise NativeExecutorError("native_process_argv_invalid")
        workspace = Path(session.workspace_path)
        cwd = self._scoped_path(workspace, request.cwd)
        if not cwd.is_dir():
            raise NativeExecutorError("native_process_cwd_invalid")
        decision, consumed = self._authorize(
            session,
            "shell",
            "exec",
            f"command://{request.argv[0]}",
            {"argv": request.argv, "cwd": str(cwd)},
        )
        started = time.monotonic()
        succeeded = False
        summary: dict[str, Any] = {}
        try:
            input_labels = self.dataflow.labels_for_scope(str(cwd))
            session_taint = self.dataflow.taint_session(session.id, input_labels)
            completed = subprocess.run(
                request.argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
                env=self._process_environment(),
            )
            stdout = completed.stdout[:MAXIMUM_NATIVE_IO_BYTES]
            stderr = completed.stderr[:MAXIMUM_NATIVE_IO_BYTES]
            succeeded = completed.returncode == 0
            summary = {
                "returncode": completed.returncode,
                "stdout_digest": _token_hash(stdout),
                "stderr_digest": _token_hash(stderr),
            }
            return self._execution_response(decision, {
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "labels": session_taint,
            })
        except subprocess.TimeoutExpired as exc:
            summary = {"timeout": True}
            raise NativeExecutorError("native_process_timeout", 504) from exc
        except OSError as exc:
            summary = {"start_failed": True}
            raise NativeExecutorError("native_process_failed", 500) from exc
        finally:
            self.gateway.record_execution_result(
                decision["id"],
                consumed["grant_id"],
                succeeded,
                summary,
                int((time.monotonic() - started) * 1000),
            )

    def declassify(self, request: NativeDeclassificationRequest) -> dict[str, Any]:
        self._session(request.session_id, request.token)
        try:
            return self.dataflow.declassify(request.session_id, request.rule_id, request.purpose)
        except DataFlowError as exc:
            raise NativeExecutorError(exc.code, exc.status_code) from exc

    async def mcp(self, request: NativeMCPRequest) -> dict[str, Any]:
        session = self._session(request.session_id, request.token)
        destination = f"mcp://{request.server_id}/{request.tool_name}"
        try:
            egress = self.dataflow.authorize_egress(session.id, "mcp", destination)
            value = await self.gateway.call_mcp(
                request.server_id,
                request.tool_name,
                MCPCallRequest.model_validate({
                    "principal": {"id": session.principal_id, "authn": "native-session-token"},
                    "endpoint_id": "agentfactor-local",
                    "agent": {"name": session.agent_name, "version": session.agent_version},
                    "integration_mode": "native",
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "cwd": session.workspace_path,
                    "arguments": request.arguments,
                }),
            )
            session_taint = self.dataflow.taint_session(session.id, {"untrusted-content"})
            return {
                **value,
                "assurance": "EXECUTOR_ENFORCED",
                "data_flow": {"egress": egress, "result_labels": ["untrusted-content"]},
                "session_taint": session_taint,
            }
        except DataFlowError as exc:
            raise NativeExecutorError(exc.code, exc.status_code) from exc
        except SecurityGatewayError as exc:
            raise NativeExecutorError(exc.code, exc.status_code) from exc

    def network_request(self, request: NativeNetworkRequest) -> dict[str, Any]:
        session = self._session(request.session_id, request.token)
        if request.body is not None and len(request.body.encode("utf-8")) > MAXIMUM_NATIVE_IO_BYTES:
            raise NativeExecutorError("native_io_too_large", 413)
        if len(request.headers) > 64 or any(len(key) > 256 or len(value) > 8192 for key, value in request.headers.items()):
            raise NativeExecutorError("network_headers_invalid")
        try:
            value = self.network.request(
                session_id=session.id,
                method=request.method,
                url=request.url,
                headers=request.headers,
                body=request.body,
                timeout_seconds=request.timeout_seconds,
                authorize=lambda resource, params: self._authorize(
                    session, "network", "request", resource, params
                ),
                record=lambda decision, grant, succeeded, summary, duration: self.gateway.record_execution_result(
                    decision["id"], grant["grant_id"], succeeded, summary, duration
                ),
            )
            session_taint = self.dataflow.taint_session(session.id, {"untrusted-content"})
            return {
                "assurance": "EXECUTOR_ENFORCED",
                "result": value,
                "result_labels": ["untrusted-content"],
                "session_taint": session_taint,
            }
        except (DataFlowError, NetworkEgressError) as exc:
            raise NativeExecutorError(exc.code, exc.status_code) from exc

    def model_request(self, request: NativeModelRequest) -> dict[str, Any]:
        session = self._session(request.session_id, request.token)
        try:
            value = self.model.complete(
                session_id=session.id,
                provider_id=request.provider_id,
                model=request.model,
                messages=request.messages,
                parameters=request.parameters,
                timeout_seconds=request.timeout_seconds,
                authorize=lambda resource, params: self._authorize(
                    session, "model", "complete", resource, params
                ),
                record=lambda decision, grant, succeeded, summary, duration: self.gateway.record_execution_result(
                    decision["id"], grant["grant_id"], succeeded, summary, duration
                ),
            )
            session_taint = self.dataflow.taint_session(session.id, {"untrusted-content"})
            return {
                "assurance": "EXECUTOR_ENFORCED",
                "result": value,
                "result_labels": ["untrusted-content"],
                "session_taint": session_taint,
            }
        except (CredentialBrokerError, DataFlowError, NetworkEgressError, ModelEgressError) as exc:
            raise NativeExecutorError(exc.code, exc.status_code) from exc

    def _session(self, session_id: str, token: str) -> SecurityNativeSession:
        if not token or len(token) > 256:
            raise NativeExecutorError("native_session_unauthorized", 403)
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            value = db.query(SecurityNativeSession).filter(
                SecurityNativeSession.id == session_id,
                SecurityNativeSession.token_hash == _token_hash(token),
                SecurityNativeSession.status == "active",
                SecurityNativeSession.expires_at > now,
            ).first()
            if value is None:
                raise NativeExecutorError("native_session_unauthorized", 403)
            return value

    @staticmethod
    def _scoped_path(workspace: Path, path: str) -> Path:
        if "\x00" in path:
            raise NativeExecutorError("native_path_invalid")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidate = candidate.resolve(strict=False)
        if candidate != workspace and workspace not in candidate.parents:
            raise NativeExecutorError("native_path_outside_workspace", 403)
        return candidate

    def _authorize(
        self,
        session: SecurityNativeSession,
        surface: str,
        operation: str,
        resource: str,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        action = CanonicalSecurityAction.model_validate({
            "principal": {"id": session.principal_id, "authn": "native-session-token"},
            "endpoint_id": "agentfactor-local",
            "agent": {"name": session.agent_name, "version": session.agent_version},
            "integration_mode": "native",
            "context": {
                "project_id": session.project_id,
                "session_id": session.id,
                "cwd": session.workspace_path,
            },
            "action": {
                "surface": surface,
                "operation": operation,
                "resource": resource,
                "params": params,
            },
        })
        try:
            decision = self.gateway.evaluate(action)
            if decision["effect"] == "block":
                raise NativeExecutorError("native_action_blocked", 403)
            consumed = self.gateway.consume(decision["execution_grant"], action)
            return decision, consumed
        except SecurityGatewayError as exc:
            raise NativeExecutorError(exc.code, exc.status_code) from exc

    @staticmethod
    def _execution_response(decision: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision": {key: value for key, value in decision.items() if key != "execution_grant"},
            "assurance": "EXECUTOR_ENFORCED",
            "result": result,
        }

    @staticmethod
    def _process_environment() -> dict[str, str]:
        allowed = {"PATH", "LANG", "LC_ALL", "TZ"}
        return {key: value for key, value in os.environ.items() if key in allowed}
