"""HTTP API for AgentFactor's security posture and minimized event store."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from agentfactor.security.service import SecuritySyncError, SecuritySyncService
from agentfactor.security.gateway import SecurityGatewayError, SecurityGatewayService
from agentfactor.security.executor import NativeExecutorError, NativeExecutorService
from agentfactor.security.policy import SecurityPolicyError, SecurityPolicyService
from agentfactor.security.access import SecurityAccessError, SecurityAccessService
from agentfactor.security.response import SecurityResponseError, SecurityResponseService
from agentfactor.security.export import SecurityAuditExporter, SecurityExportError
from agentfactor.security.sandbox import SandboxService
from agentfactor.security.schemas import (
    CanonicalSecurityAction,
    ConsumeGrantRequest,
    MCPCallRequest,
    MCPInventoryRequest,
    NativeDeclassificationRequest,
    NativeFilesystemRequest,
    NativeMCPRequest,
    NativeNetworkRequest,
    NativeModelRequest,
    NativeProcessRequest,
    NativeSessionCloseRequest,
    NativeSessionCreateRequest,
    SecurityPolicyCreateRequest,
    SecurityPolicyRevisionCreateRequest,
    RoleBindingCreateRequest,
    SecurityResponseRequest,
    PolicyRolloutCreateRequest,
)


router = APIRouter(prefix="/security/v1alpha1", tags=["security"])


def get_security_sync_service() -> SecuritySyncService:
    return SecuritySyncService()


def get_security_gateway_service() -> SecurityGatewayService:
    return SecurityGatewayService()


def get_sandbox_service() -> SandboxService:
    return SandboxService()


def get_security_policy_service() -> SecurityPolicyService:
    return SecurityPolicyService()


def get_native_executor_service() -> NativeExecutorService:
    return NativeExecutorService()


def get_security_access_service() -> SecurityAccessService:
    return SecurityAccessService()


def get_security_response_service() -> SecurityResponseService:
    return SecurityResponseService()


def get_security_audit_exporter() -> SecurityAuditExporter:
    return SecurityAuditExporter()


def authenticated_security_principal(
    authorization: str | None = Header(default=None),
    service: SecurityAccessService = Depends(get_security_access_service),
) -> str:
    try:
        return service.authenticate(authorization)
    except SecurityAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.get("/capabilities")
def capabilities(service: SecuritySyncService = Depends(get_security_sync_service)) -> dict:
    try:
        return service.capabilities().model_dump()
    except (SecuritySyncError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ACR capabilities are unavailable or incompatible") from exc


@router.post("/sync")
def sync(service: SecuritySyncService = Depends(get_security_sync_service)) -> dict:
    try:
        return service.sync()
    except SecuritySyncError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/overview")
def overview(service: SecuritySyncService = Depends(get_security_sync_service)) -> dict:
    return service.overview()


@router.get("/endpoints")
def endpoints(service: SecuritySyncService = Depends(get_security_sync_service)) -> list[dict]:
    return service.endpoints()


@router.get("/endpoints/{endpoint_id}")
def endpoint(endpoint_id: str, service: SecuritySyncService = Depends(get_security_sync_service)) -> dict:
    value = service.endpoint(endpoint_id)
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Security endpoint not found")
    return value


@router.get("/integrations/{integration_id}")
def integration(integration_id: str, service: SecuritySyncService = Depends(get_security_sync_service)) -> dict:
    value = service.integration(integration_id)
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Security integration not found")
    return value


@router.post("/integrations/{integration_id}/verify")
def verify(integration_id: str, service: SecuritySyncService = Depends(get_security_sync_service)) -> dict:
    try:
        return service.verify(integration_id)
    except SecuritySyncError as exc:
        if str(exc).startswith("Unknown security integration"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/events")
def events(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    service: SecuritySyncService = Depends(get_security_sync_service),
) -> list[dict]:
    return service.events(after_id=after_id, limit=limit)


@router.post("/actions/evaluate")
def evaluate_action(
    action: CanonicalSecurityAction,
    service: SecurityGatewayService = Depends(get_security_gateway_service),
) -> dict:
    try:
        return service.evaluate(action)
    except SecurityGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/execution-grants/consume")
def consume_execution_grant(
    request: ConsumeGrantRequest,
    service: SecurityGatewayService = Depends(get_security_gateway_service),
) -> dict:
    try:
        return service.consume(request.token, request.action)
    except SecurityGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.get("/action-decisions")
def action_decisions(
    limit: int = Query(default=200, ge=1, le=500),
    service: SecurityGatewayService = Depends(get_security_gateway_service),
) -> list[dict]:
    return service.decisions(limit)


@router.get("/alerts")
def alerts(
    limit: int = Query(default=200, ge=1, le=500),
    service: SecurityGatewayService = Depends(get_security_gateway_service),
) -> list[dict]:
    return service.alerts(limit)


@router.get("/sandboxes")
def sandboxes(service: SandboxService = Depends(get_sandbox_service)) -> list[dict]:
    return service.sessions()


@router.get("/sandboxes/{sandbox_id}")
def sandbox(sandbox_id: str, service: SandboxService = Depends(get_sandbox_service)) -> dict:
    value = service.session(sandbox_id)
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Security sandbox not found")
    return value


@router.get("/mcp/servers")
def mcp_servers(service: SecurityGatewayService = Depends(get_security_gateway_service)) -> list[dict]:
    try:
        return service.mcp_inventory()
    except SecurityGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/mcp/inventory")
async def authorized_mcp_inventory(
    request: MCPInventoryRequest,
    service: SecurityGatewayService = Depends(get_security_gateway_service),
) -> dict:
    try:
        return await service.list_mcp_for_agent(request)
    except SecurityGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/mcp/discover")
async def discover_mcp(service: SecurityGatewayService = Depends(get_security_gateway_service)) -> list[dict]:
    try:
        return await service.discover_mcp()
    except SecurityGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/mcp/servers/{server_id}/tools/{tool_name}/call")
async def call_mcp_tool(
    server_id: str,
    tool_name: str,
    request: MCPCallRequest,
    service: SecurityGatewayService = Depends(get_security_gateway_service),
) -> dict:
    try:
        return await service.call_mcp(server_id, tool_name, request)
    except SecurityGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/policies")
def create_policy(
    request: SecurityPolicyCreateRequest,
    service: SecurityPolicyService = Depends(get_security_policy_service),
) -> dict:
    try:
        return service.create_policy(request)
    except SecurityPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.get("/policies")
def policies(service: SecurityPolicyService = Depends(get_security_policy_service)) -> list[dict]:
    return service.policies()


@router.post("/policies/{policy_id}/revisions")
def create_policy_revision(
    policy_id: str,
    request: SecurityPolicyRevisionCreateRequest,
    service: SecurityPolicyService = Depends(get_security_policy_service),
) -> dict:
    try:
        return service.create_revision(policy_id, request)
    except SecurityPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.get("/policies/{policy_id}/revisions")
def policy_revisions(
    policy_id: str,
    service: SecurityPolicyService = Depends(get_security_policy_service),
) -> list[dict]:
    try:
        return service.revisions(policy_id)
    except SecurityPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/policies/{policy_id}/revisions/{revision_id}/publish")
def publish_policy_revision(
    policy_id: str,
    revision_id: str,
    service: SecurityPolicyService = Depends(get_security_policy_service),
) -> dict:
    try:
        return service.publish(policy_id, revision_id)
    except SecurityPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/policies/{policy_id}/revisions/{revision_id}/rollouts")
def create_policy_rollout(
    policy_id: str,
    revision_id: str,
    request: PolicyRolloutCreateRequest,
    principal_id: str = Depends(authenticated_security_principal),
    access: SecurityAccessService = Depends(get_security_access_service),
    service: SecurityPolicyService = Depends(get_security_policy_service),
) -> dict:
    try:
        access.authorize(principal_id, "policy:publish")
        return service.create_rollout(policy_id, revision_id, request.percentage, principal_id)
    except (SecurityAccessError, SecurityPolicyError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.get("/policies/{policy_id}/verify")
def verify_active_policy_gateway(
    policy_id: str,
    service: SecurityPolicyService = Depends(get_security_policy_service),
) -> dict:
    try:
        return service.verify_active_gateway(policy_id)
    except SecurityPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/sessions")
def create_native_session(
    request: NativeSessionCreateRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return service.create_session(request)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/sessions/{session_id}/close")
def close_native_session(
    session_id: str,
    request: NativeSessionCloseRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return service.close_session(session_id, request.token)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/filesystem")
def execute_native_filesystem(
    request: NativeFilesystemRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return service.filesystem(request)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/process")
def execute_native_process(
    request: NativeProcessRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return service.process(request)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/mcp")
async def execute_native_mcp(
    request: NativeMCPRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return await service.mcp(request)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/network")
def execute_native_network(
    request: NativeNetworkRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return service.network_request(request)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/model")
def execute_native_model(
    request: NativeModelRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return service.model_request(request)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/native/declassify")
def declassify_native_session(
    request: NativeDeclassificationRequest,
    service: NativeExecutorService = Depends(get_native_executor_service),
) -> dict:
    try:
        return service.declassify(request)
    except NativeExecutorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/admin/role-bindings")
def create_role_binding(
    request: RoleBindingCreateRequest,
    principal_id: str = Depends(authenticated_security_principal),
    service: SecurityAccessService = Depends(get_security_access_service),
) -> dict:
    try:
        return service.bind(principal_id, request.principal_id, request.role)
    except SecurityAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def _authorize_response(principal_id: str, access: SecurityAccessService) -> None:
    try:
        access.authorize(principal_id, "response:execute")
    except SecurityAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/response/sessions/{session_id}/block")
def block_native_session(
    session_id: str,
    request: SecurityResponseRequest,
    principal_id: str = Depends(authenticated_security_principal),
    access: SecurityAccessService = Depends(get_security_access_service),
    service: SecurityResponseService = Depends(get_security_response_service),
) -> dict:
    _authorize_response(principal_id, access)
    try:
        return service.session_status(session_id, True, principal_id, request.reason)
    except SecurityResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/response/sessions/{session_id}/unblock")
def unblock_native_session(
    session_id: str,
    request: SecurityResponseRequest,
    principal_id: str = Depends(authenticated_security_principal),
    access: SecurityAccessService = Depends(get_security_access_service),
    service: SecurityResponseService = Depends(get_security_response_service),
) -> dict:
    _authorize_response(principal_id, access)
    try:
        return service.session_status(session_id, False, principal_id, request.reason)
    except SecurityResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/response/endpoints/{endpoint_id}/quarantine")
def quarantine_security_endpoint(
    endpoint_id: str,
    request: SecurityResponseRequest,
    principal_id: str = Depends(authenticated_security_principal),
    access: SecurityAccessService = Depends(get_security_access_service),
    service: SecurityResponseService = Depends(get_security_response_service),
) -> dict:
    _authorize_response(principal_id, access)
    try:
        return service.endpoint_status(endpoint_id, True, principal_id, request.reason)
    except SecurityResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/response/endpoints/{endpoint_id}/release")
def release_security_endpoint(
    endpoint_id: str,
    request: SecurityResponseRequest,
    principal_id: str = Depends(authenticated_security_principal),
    access: SecurityAccessService = Depends(get_security_access_service),
    service: SecurityResponseService = Depends(get_security_response_service),
) -> dict:
    _authorize_response(principal_id, access)
    try:
        return service.endpoint_status(endpoint_id, False, principal_id, request.reason)
    except SecurityResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/admin/audit/export")
def export_security_audit(
    principal_id: str = Depends(authenticated_security_principal),
    access: SecurityAccessService = Depends(get_security_access_service),
    exporter: SecurityAuditExporter = Depends(get_security_audit_exporter),
) -> dict:
    try:
        access.authorize(principal_id, "*")
        return exporter.export()
    except (SecurityAccessError, SecurityExportError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
