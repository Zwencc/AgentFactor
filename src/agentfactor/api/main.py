from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel as PydanticBaseModel
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agentfactor.clients.database import TerminalMetrics as TerminalMetricsRow
from agentfactor.clients.database import WorkItem as WorkItemRow
from agentfactor.clients.database import WorkGraphGenerationRequest as GenerationRequestRow
from agentfactor.clients.database import init_db, session_scope
from agentfactor.models.context import ContextPackRequest, DifferentialPackRequest
from agentfactor.models.topology import CapabilityEstimateResponse, TopologyProposalResponse
from agentfactor.models.work import (
    WorkEdgeCreateRequest,
    WorkEdgeResponse,
    WorkGraphResponse,
    WorkItemCreateRequest,
    WorkItemResponse,
    WorkItemUpdateRequest,
)
from agentfactor.models.approval import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalRequest,
)
from agentfactor.models.enums import ApprovalStatus, WorkItemStatus
from agentfactor.models.flow import Flow, FlowCreateRequest
from agentfactor.models.inbox import InboxCreateRequest, InboxMessage
from agentfactor.models.review import (
    LLMProviderCreateRequest,
    LLMProviderResponse,
    LLMProviderTestRequest,
    LLMProviderTestResponse,
    LLMProviderUpdateRequest,
    TerminalReviewRequest,
    TerminalReviewResponse,
    VerifierRunResponse,
)
from agentfactor.models.session import Session, SessionCreateRequest
from agentfactor.models.terminal import (
    Terminal as TerminalModel,
)
from agentfactor.models.terminal import (
    TerminalCreateRequest,
    TerminalInputRequest,
)
from agentfactor.providers.base import ProviderInitializationError
from agentfactor.providers.manager import ProviderManager
from agentfactor.services.approval_service import ApprovalService
from agentfactor.services.cleanup_service import CleanupService
from agentfactor.services.event_service import EventService
from agentfactor.services.flow_service import FlowService
from agentfactor.services.inbox_service import InboxService
from agentfactor.services.llm_review_service import LLMReviewService
from agentfactor.services.capability_registry import CapabilityRegistry
from agentfactor.services.compaction_service import CompactionService
from agentfactor.services.context_loss_detector import ContextLossDetector
from agentfactor.services.context_pack_service import ContextPackService
from agentfactor.services.embedding_service import EmbeddingService
from agentfactor.services.proof_collector import ProofCollector
from agentfactor.services.topology_engine import TopologyEngine
from agentfactor.services.prompt_service import PromptWatcher
from agentfactor.services.session_service import SessionService
from agentfactor.services.stream_processor import StreamProcessor
from agentfactor.services.terminal_service import TerminalService
from agentfactor.services.work_service import ProofRequiredError, WorkService
from agentfactor.utils import agent_profiles
from agentfactor.utils.agent_profiles import AgentProfileError, load_agent_profile
from agentfactor.utils.logging import setup_logging
from agentfactor.utils.pathing import ensure_runtime_directories
from agentfactor.utils.wsl import command_exists

app = FastAPI(title="AgentFactor API", version="0.1.0")
ADMIN_DIST_DIR = Path(__file__).resolve().parents[3] / "frontend_fantastic" / "apps" / "core" / "dist"

PROVIDER_CATALOG = [
    {"key": "claude_code", "label": "Claude Code",  "binary": "claude",    "mcp_capable": True},
    {"key": "codex",       "label": "OpenAI Codex", "binary": "codex",     "mcp_capable": False},
    {"key": "deepseek",    "label": "DeepSeek",     "binary": "deepcode",  "mcp_capable": False},
    {"key": "q_cli",       "label": "Amazon Q CLI", "binary": "q",         "mcp_capable": False},
]

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_RISK_PATTERNS = [
    ("rm -rf", re.compile(r"\brm\s+-rf\b")),
    ("git reset --hard", re.compile(r"\bgit\s+reset\s+--hard\b")),
    ("sudo", re.compile(r"\bsudo\b")),
    ("curl pipe shell", re.compile(r"\bcurl\b.*\|\s*(?:sh|bash)\b")),
    ("pip break-system-packages", re.compile(r"\bpip(?:3)?\s+install\b.*--break-system-packages\b")),
]

_BLUEPRINT_FATAL_PATTERNS = [
    (
        re.compile(
            r"(API Error:\s*401|Invalid authentication credentials|Please run /login)",
            re.IGNORECASE,
        ),
        "Claude Code is not authenticated. Run `/login` in Claude Code or refresh its credentials, then start generation again.",
    ),
]
_TERMINAL_IDLE_ALERT_MINUTES = 15.0


def _risk_hints(command_text: str) -> list[str]:
    return [label for label, pattern in _RISK_PATTERNS if pattern.search(command_text)]


def _blueprint_generation_error(history: Optional[str]) -> Optional[str]:
    if not history:
        return None
    for pattern, message in _BLUEPRINT_FATAL_PATTERNS:
        if pattern.search(history):
            return message
    return None


def _terminal_lookup(sessions) -> dict[str, dict[str, str]]:
    lookup = {}
    for session in sessions:
        for terminal in session.terminals:
            lookup[terminal.id] = {
                "id": terminal.id,
                "label": f"{terminal.window_name} ({terminal.provider})",
                "session_name": session.name,
            }
    return lookup


def _approval_payloads(approvals, terminals: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    payloads = []
    for approval in approvals:
        payload = approval.model_dump(mode="json")
        payload["terminal"] = terminals.get(approval.terminal_id)
        payload["supervisor"] = terminals.get(approval.supervisor_id)
        payload["risk_hints"] = _risk_hints(approval.command_text)
        payloads.append(payload)
    return payloads


def _prompt_payloads(inbox_summary, terminals: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    prompts = []
    seen: set[tuple[str | None, str]] = set()
    _extract_patterns = [
        r"acd send\s+(\S+)",
        r"agentfactor send\s+(\S+)",
        r"acd s\s+(\S+)",
        r"agentfactor s\s+(\S+)",
    ]
    for supervisor_id, messages in inbox_summary.items():
        for message in messages:
            if "[PROMPT]" not in message.message:
                continue
            target_id: str | None = None
            for pat in _extract_patterns:
                m = re.search(pat, message.message)
                if m:
                    target_id = m.group(1)
                    break
            dedupe_key = (target_id, message.message)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            lines = message.message.splitlines()
            prompts.append(
                {
                    "id": message.id,
                    "supervisor_id": supervisor_id,
                    "target_id": target_id,
                    "target": terminals.get(target_id) if target_id else None,
                    "title": lines[0] if lines else message.message,
                    "body": "\n".join(lines[1:]),
                    "created_at": message.created_at.isoformat()
                    if getattr(message.created_at, "isoformat", None)
                    else message.created_at,
                }
            )
    return prompts


def _terminal_alert_payloads(terminals: list[TerminalModel]) -> list[dict[str, Any]]:
    latest_metrics: dict[str, TerminalMetricsRow] = {}
    if terminals:
        with session_scope() as db:
            for terminal in terminals:
                metric = (
                    db.query(TerminalMetricsRow)
                    .filter(TerminalMetricsRow.terminal_id == terminal.id)
                    .order_by(TerminalMetricsRow.sampled_at.desc())
                    .first()
                )
                if metric is not None:
                    latest_metrics[terminal.id] = metric

    alerts: list[dict[str, Any]] = []
    for terminal in terminals:
        status_value = terminal.status.value if hasattr(terminal.status, "value") else str(terminal.status)
        if status_value == "ERROR":
            alerts.append(
                {
                    "id": f"error:{terminal.id}",
                    "terminal_id": terminal.id,
                    "session_name": terminal.session_name,
                    "window_name": terminal.window_name,
                    "severity": "error",
                    "kind": "error",
                    "message": "Terminal is in ERROR status; inspect output/logs and close or restart it.",
                    "idle_streak_minutes": None,
                }
            )
            continue

        metric = latest_metrics.get(terminal.id)
        if (
            status_value in {"READY", "RUNNING"}
            and metric is not None
            and metric.idle_streak_minutes >= _TERMINAL_IDLE_ALERT_MINUTES
        ):
            alerts.append(
                {
                    "id": f"idle:{terminal.id}",
                    "terminal_id": terminal.id,
                    "session_name": terminal.session_name,
                    "window_name": terminal.window_name,
                    "severity": "warning",
                    "kind": "idle",
                    "message": (
                        f"Terminal has been idle for {metric.idle_streak_minutes:.1f} minutes; "
                        "inspect it or close/restart the session."
                    ),
                    "idle_streak_minutes": metric.idle_streak_minutes,
                }
            )
    return alerts


def _directory_entry(path: Path) -> dict[str, Any]:
    return {
        "name": path.name or str(path),
        "path": str(path),
    }


def _directory_shortcuts() -> list[dict[str, Any]]:
    shortcuts = [
        {"label": "Project", "path": str(PROJECT_ROOT)},
        {"label": "Current", "path": str(Path.cwd())},
        {"label": "Home", "path": str(Path.home())},
    ]
    seen: set[str] = set()
    unique = []
    for item in shortcuts:
        if item["path"] not in seen and Path(item["path"]).exists():
            seen.add(item["path"])
            unique.append(item)
    return unique


if (ADMIN_DIST_DIR / "assets").exists():
    app.mount(
        "/admin/assets",
        StaticFiles(directory=str(ADMIN_DIST_DIR / "assets")),
        name="admin-assets",
    )


@app.get("/", include_in_schema=False)
async def root_dashboard() -> RedirectResponse:
    return RedirectResponse(url="/admin")


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_index() -> FileResponse:
    index_path = ADMIN_DIST_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Fantastic-admin dashboard has not been built. Run pnpm build in frontend_fantastic.",
        )
    return FileResponse(index_path)


@app.get("/admin/{path:path}", include_in_schema=False)
async def admin_spa(path: str) -> FileResponse:
    target = (ADMIN_DIST_DIR / path).resolve()
    if target.is_file() and ADMIN_DIST_DIR.resolve() in target.parents:
        return FileResponse(target)
    index_path = ADMIN_DIST_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Fantastic-admin dashboard has not been built. Run pnpm build in frontend_fantastic.",
        )
    return FileResponse(index_path)


def _require_service(name: str):
    service = getattr(app.state, name, None)
    if service is None:
        raise RuntimeError(f"Service '{name}' not initialised.")
    return service


def get_terminal_service() -> TerminalService:
    return _require_service("terminal_service")


def get_session_service() -> SessionService:
    return _require_service("session_service")


def get_inbox_service() -> InboxService:
    return _require_service("inbox_service")


def get_flow_service() -> FlowService:
    return _require_service("flow_service")


def get_approval_service() -> ApprovalService:
    return _require_service("approval_service")


def get_event_service() -> EventService:
    return _require_service("event_service")


def get_stream_processor() -> StreamProcessor:
    return _require_service("stream_processor")


def get_work_service() -> WorkService:
    return _require_service("work_service")


def get_llm_review_service() -> LLMReviewService:
    return _require_service("llm_review_service")


def get_context_pack_service() -> ContextPackService:
    return _require_service("context_pack_service")


def get_capability_registry() -> CapabilityRegistry:
    return _require_service("capability_registry")


def get_topology_engine() -> TopologyEngine:
    return _require_service("topology_engine")


def get_compaction_service() -> CompactionService:
    return _require_service("compaction_service")


@app.on_event("startup")
async def startup_event() -> None:
    setup_logging()
    ensure_runtime_directories()
    init_db()
    provider_manager = ProviderManager()
    terminal_service = TerminalService(providers=provider_manager)
    inbox_service = InboxService(terminal_service)
    flow_service = FlowService()
    approval_service = ApprovalService(terminal_service, inbox_service)
    session_service = SessionService(terminal_service)
    cleanup_service = CleanupService(terminal_service)
    prompt_watcher = PromptWatcher(session_service, terminal_service, inbox_service, approval_service)
    event_service = EventService()
    stream_processor = StreamProcessor(terminal_service, event_service)
    work_service = WorkService()
    llm_review_service = LLMReviewService()
    capability_registry = CapabilityRegistry()
    proof_collector = ProofCollector(event_service, inbox_service, work_service, capability_registry)
    embedding_service = EmbeddingService()
    context_pack_service = ContextPackService(embedding_service, event_service)
    context_loss_detector = ContextLossDetector(event_service, inbox_service, context_pack_service)
    topology_engine = TopologyEngine(event_service, capability_registry, inbox_service)
    compaction_service = CompactionService(event_service, inbox_service)

    app.state.provider_manager = provider_manager
    app.state.terminal_service = terminal_service
    app.state.inbox_service = inbox_service
    app.state.flow_service = flow_service
    app.state.approval_service = approval_service
    app.state.session_service = session_service
    app.state.cleanup_service = cleanup_service
    app.state.prompt_watcher = prompt_watcher
    app.state.event_service = event_service
    app.state.stream_processor = stream_processor
    app.state.work_service = work_service
    app.state.llm_review_service = llm_review_service
    app.state.proof_collector = proof_collector
    app.state.embedding_service = embedding_service
    app.state.context_pack_service = context_pack_service
    app.state.context_loss_detector = context_loss_detector
    app.state.capability_registry = capability_registry
    app.state.topology_engine = topology_engine
    app.state.compaction_service = compaction_service
    app.state.background_tasks = [
        asyncio.create_task(_cleanup_loop(cleanup_service)),
        asyncio.create_task(_idle_loop(cleanup_service)),
        asyncio.create_task(_inbox_loop(inbox_service)),
        asyncio.create_task(_prompt_loop(prompt_watcher)),
        asyncio.create_task(_flow_loop(flow_service, terminal_service)),
        asyncio.create_task(_stream_loop(stream_processor)),
        asyncio.create_task(_proof_collector_loop(proof_collector)),
        asyncio.create_task(_context_loss_loop(context_loss_detector)),
        asyncio.create_task(_topology_loop(topology_engine)),
        asyncio.create_task(_compaction_loop(compaction_service)),
    ]


async def _cleanup_loop(cleanup_service: CleanupService) -> None:
    while True:
        cleanup_service.purge_completed_terminals()
        cleanup_service.purge_orphan_logs()
        await asyncio.sleep(3600)


async def _idle_loop(cleanup_service: CleanupService) -> None:
    while True:
        await asyncio.sleep(300)  # first run after 5 min, not at startup
        cleanup_service.purge_idle_terminals()


async def _inbox_loop(inbox_service: InboxService) -> None:
    while True:
        inbox_service.deliver_all_pending()
        await asyncio.sleep(5)


async def _prompt_loop(prompt_watcher: PromptWatcher) -> None:
    while True:
        prompt_watcher.scan()
        await asyncio.sleep(10)


async def _stream_loop(stream_processor: StreamProcessor) -> None:
    await stream_processor.run()


async def _proof_collector_loop(proof_collector: ProofCollector) -> None:
    await proof_collector.run()


async def _context_loss_loop(detector: ContextLossDetector) -> None:
    await detector.run()


async def _topology_loop(engine: TopologyEngine) -> None:
    await engine.run()


async def _compaction_loop(service: CompactionService) -> None:
    await service.run()


async def _flow_loop(flow_service: FlowService, terminal_service: TerminalService) -> None:
    while True:
        now = datetime.utcnow()
        for flow in flow_service.list_flows():
            if not flow.enabled or flow.next_run is None or flow.next_run > now:
                continue
            try:
                _execute_flow(flow, terminal_service)
                next_run = flow_service.compute_next_run(flow.schedule, now)
                flow_service.record_run(flow.name, last_run=now, next_run=next_run)
            except Exception:
                # Keep the scheduler alive; manual `/flows/{name}/run` exposes errors directly.
                flow_service.record_run(flow.name, last_run=now, next_run=None)
        await asyncio.sleep(30)


def _flow_provider(agent_profile: str) -> str:
    try:
        profile = load_agent_profile(agent_profile)
    except AgentProfileError:
        return "claude_code"
    return profile.default_provider or "claude_code"


def _flow_message(flow: Flow) -> str:
    if flow.script:
        return flow.script

    path = Path(flow.file_path)
    if path.exists() and path.is_file():
        return f"Execute flow '{flow.name}' from {path}:\n\n{path.read_text(encoding='utf-8')}"
    return f"Execute flow '{flow.name}' from {flow.file_path}."


def _execute_flow(flow: Flow, terminal_service: TerminalService) -> TerminalModel:
    working_directory = str(Path(flow.file_path).resolve().parent) if flow.file_path else None
    terminal = terminal_service.create_terminal(
        provider_key=_flow_provider(flow.agent_profile),
        role="flow",
        agent_profile=flow.agent_profile,
        working_directory=working_directory,
    )
    terminal_service.send_input(terminal.id, _flow_message(flow))
    return terminal


@app.on_event("shutdown")
async def shutdown_event() -> None:
    tasks = getattr(app.state, "background_tasks", [])
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@app.get("/health")
async def health(terminals: TerminalService = Depends(get_terminal_service)) -> dict[str, Any]:
    """Health check endpoint."""
    sessions = getattr(app.state, "session_service", None)
    all_terminals: list[TerminalModel] = []
    if sessions is not None:
        for session in sessions.list_sessions():
            all_terminals.extend(session.terminals)
    else:
        all_terminals = []
    return {
        "status": "ok",
        "server": "running",
        "terminals": {
            "total": len(all_terminals),
            "ready": sum(1 for t in all_terminals if t.status.value == "READY"),
            "running": sum(1 for t in all_terminals if t.status.value == "RUNNING"),
            "completed": sum(1 for t in all_terminals if t.status.value == "COMPLETED"),
            "error": sum(1 for t in all_terminals if t.status.value == "ERROR"),
        },
    }


@app.get("/events")
async def list_events(
    terminal_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
    since_cursor: int = 0,
    events: EventService = Depends(get_event_service),
) -> list[dict[str, Any]]:
    """Return recent events from the immutable event log."""
    return events.get_recent(
        terminal_id=terminal_id,
        event_type=event_type,
        limit=min(limit, 500),
        since_cursor=since_cursor,
    )


@app.get("/terminals/{terminal_id}/metrics")
async def get_terminal_metrics(
    terminal_id: str,
    processor: StreamProcessor = Depends(get_stream_processor),
) -> dict[str, Any]:
    """Return the latest sampled health metrics for a terminal."""
    metrics = processor.get_latest_metrics(terminal_id)
    if metrics is None:
        raise HTTPException(status_code=404, detail=f"No metrics yet for terminal '{terminal_id}'")
    return metrics


@app.get("/personas")
async def list_personas() -> list[dict[str, Any]]:
    """Return all known agent personas in a UI-friendly flat catalog."""
    catalog = agent_profiles.get_persona_catalog()
    personas: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in ("installed", "bundled"):
        for entry in catalog[source]:
            name = entry["name"]
            if name in seen:
                continue
            seen.add(name)
            personas.append(
                {
                    "name": name,
                    "description": entry.get("description", ""),
                    "default_provider": entry.get("default_provider") or "claude_code",
                    "source": entry.get("source", source),
                    "scope": entry.get("scope", source),
                    "tags": entry.get("tags", []),
                }
            )
    return sorted(personas, key=lambda item: item["name"])


@app.get("/providers")
async def list_providers() -> list[dict[str, Any]]:
    """Return registered provider metadata for dashboard selection."""
    return PROVIDER_CATALOG


@app.get("/providers/health")
async def provider_health() -> list[dict[str, Any]]:
    """Check whether provider backing binaries are available."""
    results = []
    for provider in PROVIDER_CATALOG:
        binary = provider["binary"]
        available = command_exists(binary)
        results.append(
            {
                **provider,
                "available": available,
                "reason": ""
                if available
                else f"Required binary '{binary}' was not found on PATH or in WSL.",
            }
        )
    return results


@app.get("/filesystem/directories")
async def list_directories(
    path: Optional[str] = Query(default=None, description="Directory path to browse."),
) -> dict[str, Any]:
    current = Path(path).expanduser() if path else PROJECT_ROOT
    try:
        current = current.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot resolve directory: {exc}") from exc

    if not current.exists():
        raise HTTPException(status_code=404, detail="Directory does not exist.")
    if not current.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory.")

    children: list[dict[str, Any]] = []
    try:
        for child in current.iterdir():
            try:
                if child.is_dir():
                    children.append(_directory_entry(child.resolve()))
            except OSError:
                continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Directory cannot be read.") from exc

    children.sort(key=lambda item: item["name"].lower())
    parent = None if current.parent == current else str(current.parent)
    return {
        "path": str(current),
        "parent": parent,
        "children": children,
        "shortcuts": _directory_shortcuts(),
    }


class CreateDirectoryRequest(PydanticBaseModel):
    path: str
    name: str


@app.post("/filesystem/directories")
async def create_directory(body: CreateDirectoryRequest) -> dict[str, Any]:
    try:
        parent = Path(body.path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot resolve path: {exc}") from exc

    if not parent.exists() or not parent.is_dir():
        raise HTTPException(status_code=404, detail="Parent directory does not exist.")

    name = body.name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid directory name.")

    new_dir = parent / name
    if new_dir.exists():
        raise HTTPException(status_code=409, detail="Directory already exists.")

    try:
        new_dir.mkdir()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot create directory: {exc}") from exc

    return _directory_entry(new_dir)


@app.get("/dashboard/state")
async def dashboard_state(
    sessions: SessionService = Depends(get_session_service),
    inbox: InboxService = Depends(get_inbox_service),
    approvals: ApprovalService = Depends(get_approval_service),
) -> dict[str, Any]:
    """Return one consolidated dashboard polling snapshot."""
    session_list = sessions.list_sessions()
    terminals = [terminal for session in session_list for terminal in session.terminals]
    pending_approvals = approvals.list_requests(ApprovalStatus.PENDING)
    all_approvals = approvals.list_requests()
    supervisor_terminals = [
        terminal for terminal in terminals if terminal.window_name.startswith("supervisor-")
    ]
    pending_prompts = [
        message
        for terminal in supervisor_terminals
        for message in inbox.list_messages(terminal.id)
        if "[PROMPT]" in message.message
    ]
    inbox_summary = {
        terminal.id: inbox.list_messages(terminal.id) for terminal in supervisor_terminals
    }
    terminal_lookup = _terminal_lookup(session_list)
    status_counts = {
        "total": len(terminals),
        "ready": sum(1 for t in terminals if t.status.value == "READY"),
        "running": sum(1 for t in terminals if t.status.value == "RUNNING"),
        "completed": sum(1 for t in terminals if t.status.value == "COMPLETED"),
        "error": sum(1 for t in terminals if t.status.value == "ERROR"),
    }
    return {
        "health": {"status": "ok", "terminals": status_counts},
        "sessions": [session.model_dump(mode="json") for session in session_list],
        "pending_prompt_count": len(pending_prompts),
        "terminal_alerts": _terminal_alert_payloads(terminals),
        "prompt_items": _prompt_payloads(inbox_summary, terminal_lookup),
        "pending_approvals": _approval_payloads(pending_approvals, terminal_lookup),
        "approvals": _approval_payloads(all_approvals, terminal_lookup),
        "approvals_summary": {
            "pending": len(pending_approvals),
            "approved": sum(1 for approval in all_approvals if approval.status == ApprovalStatus.APPROVED),
            "denied": sum(1 for approval in all_approvals if approval.status == ApprovalStatus.DENIED),
            "total": len(all_approvals),
        },
        "providers": await provider_health(),
    }


@app.post("/sessions", response_model=TerminalModel, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    terminals: TerminalService = Depends(get_terminal_service),
) -> TerminalModel:
    created_workers: list[TerminalModel] = []
    supervisor: TerminalModel | None = None
    try:
        supervisor = terminals.create_terminal(
            provider_key=payload.provider,
            role=payload.role,
            agent_profile=payload.agent_profile,
            working_directory=payload.working_directory,
        )

        for worker_request in payload.workers:
            created_workers.append(
                terminals.create_terminal(
                    provider_key=worker_request.provider,
                    role=worker_request.role,
                    agent_profile=worker_request.agent_profile,
                    session_name=supervisor.session_name,
                    working_directory=worker_request.working_directory or payload.working_directory,
                )
            )

        return supervisor
    except ProviderInitializationError as exc:
        # Cleanup any partially created terminals to keep state consistent.
        for worker_terminal in created_workers:
            terminals.delete_terminal(worker_terminal.id)
        if supervisor is not None:
            terminals.delete_terminal(supervisor.id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sessions", response_model=List[Session])
async def list_sessions(
    sessions: SessionService = Depends(get_session_service),
) -> List[Session]:
    return sessions.list_sessions()


@app.get("/sessions/{session_name}", response_model=Session)
async def get_session(
    session_name: str,
    sessions: SessionService = Depends(get_session_service),
) -> Session:
    session_models = sessions.list_sessions()
    for session in session_models:
        if session.name == session_name:
            return session
    raise HTTPException(status_code=404, detail="Session not found.")


@app.delete("/sessions/{session_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_name: str,
    sessions: SessionService = Depends(get_session_service),
) -> None:
    sessions.delete_session(session_name)


@app.post(
    "/sessions/{session_name}/terminals",
    response_model=TerminalModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_worker_terminal(
    session_name: str,
    payload: TerminalCreateRequest,
    terminals: TerminalService = Depends(get_terminal_service),
) -> TerminalModel:
    try:
        return terminals.create_terminal(
            provider_key=payload.provider,
            role=payload.role,
            agent_profile=payload.agent_profile,
            session_name=session_name,
            working_directory=payload.working_directory,
        )
    except ProviderInitializationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/terminals/{terminal_id}", response_model=TerminalModel)
async def get_terminal(
    terminal_id: str,
    terminals: TerminalService = Depends(get_terminal_service),
) -> TerminalModel:
    terminal = terminals.get_terminal(terminal_id)
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found.")
    return terminal


@app.post("/terminals/{terminal_id}/input")
async def send_terminal_input(
    terminal_id: str,
    payload: TerminalInputRequest,
    terminals: TerminalService = Depends(get_terminal_service),
    approvals: ApprovalService = Depends(get_approval_service),
) -> dict[str, Any]:
    if payload.requires_approval:
        if not payload.supervisor_id:
            raise HTTPException(status_code=400, detail="supervisor_id is required when requesting approval.")
        approval = approvals.request_approval(
            terminal_id=terminal_id,
            supervisor_id=payload.supervisor_id,
            command_text=payload.message,
            metadata_payload=payload.metadata_payload,
        )
        return {"status": "queued_for_approval", "approval": approval.model_dump()}
    terminals.send_input(terminal_id, payload.message)
    return {"status": "sent"}


@app.get("/terminals/{terminal_id}/output")
async def get_terminal_output(
    terminal_id: str,
    mode: str = "full",
    terminals: TerminalService = Depends(get_terminal_service),
) -> dict[str, str]:
    last_only = mode == "last"
    output = terminals.capture_output(terminal_id, last_only=last_only)
    return {"output": output}


@app.delete("/terminals/{terminal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_terminal(
    terminal_id: str,
    terminals: TerminalService = Depends(get_terminal_service),
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> None:
    terminals.delete_terminal(terminal_id)
    try:
        reviews.review_terminal(terminal_id, trigger_source="terminal_deleted")
    except Exception:
        # Deletion must not fail because optional review infrastructure is unavailable.
        pass


@app.get("/terminals/{terminal_id}/analysis")
async def get_terminal_analysis(terminal_id: str) -> dict:
    """Return the saved behaviour analysis for a terminal (persists after terminal is deleted)."""
    from agentfactor.services.analysis_service import AnalysisService
    result = AnalysisService().get_analysis(terminal_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No analysis found for this terminal")
    return result


@app.get("/terminal-analyses")
async def list_terminal_analyses(limit: int = 50) -> list:
    """Return recent terminal behaviour analyses, newest first."""
    from agentfactor.services.analysis_service import AnalysisService
    return AnalysisService().list_analyses(limit=limit)


@app.post("/terminals/{terminal_id}/analysis", status_code=status.HTTP_201_CREATED)
async def trigger_terminal_analysis(
    terminal_id: str,
    run_review: bool = False,
    terminals: TerminalService = Depends(get_terminal_service),
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> dict:
    """Manually trigger (or re-run) analysis for a terminal."""
    from agentfactor.services.analysis_service import AnalysisService
    terminal = terminals.get_terminal(terminal_id)
    result = AnalysisService().analyze_terminal(
        terminal_id=terminal_id,
        session_name=terminal.session_name if terminal else None,
        window_name=terminal.window_name if terminal else None,
        provider=terminal.provider if terminal else None,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No log file found for this terminal")
    if run_review:
        result["review"] = reviews.review_terminal(terminal_id, trigger_source="manual")
    return result


@app.get("/llm-providers", response_model=List[LLMProviderResponse])
async def list_llm_providers(
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> List[LLMProviderResponse]:
    return reviews.list_providers()


@app.post("/llm-providers", response_model=LLMProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_provider(
    payload: LLMProviderCreateRequest,
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> LLMProviderResponse:
    return reviews.create_provider(payload)


@app.put("/llm-providers/{provider_id}", response_model=LLMProviderResponse)
async def update_llm_provider(
    provider_id: int,
    payload: LLMProviderUpdateRequest,
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> LLMProviderResponse:
    result = reviews.update_provider(provider_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail=f"LLM provider '{provider_id}' not found.")
    return result


@app.delete("/llm-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_provider(
    provider_id: int,
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> None:
    if not reviews.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail=f"LLM provider '{provider_id}' not found.")


@app.post("/llm-providers/{provider_id}/activate", response_model=LLMProviderResponse)
async def activate_llm_provider(
    provider_id: int,
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> LLMProviderResponse:
    result = reviews.activate_provider(provider_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"LLM provider '{provider_id}' not found.")
    return result


@app.post("/llm-providers/{provider_id}/test", response_model=LLMProviderTestResponse)
async def test_llm_provider(
    provider_id: int,
    payload: LLMProviderTestRequest,
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> LLMProviderTestResponse:
    return reviews.test_provider(provider_id, payload.prompt)


@app.post("/terminals/{terminal_id}/review", response_model=TerminalReviewResponse)
async def run_terminal_review(
    terminal_id: str,
    payload: TerminalReviewRequest = TerminalReviewRequest(),
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> TerminalReviewResponse:
    result = reviews.review_terminal(
        terminal_id,
        force=payload.force,
        trigger_source=payload.trigger_source,
        threshold=payload.threshold,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No analysis found for this terminal")
    return result


@app.get("/terminals/{terminal_id}/review", response_model=TerminalReviewResponse)
async def get_terminal_review(
    terminal_id: str,
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> TerminalReviewResponse:
    result = reviews.get_terminal_review(terminal_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No analysis found for this terminal")
    return result


@app.get("/terminals/{terminal_id}/raw-log")
async def get_terminal_raw_log(terminal_id: str) -> dict:
    """Return the stored raw log text for a terminal."""
    from agentfactor.services.analysis_service import AnalysisService
    raw = AnalysisService().get_raw_log(terminal_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="No log found for this terminal")
    return {"terminal_id": terminal_id, "raw_log": raw}


@app.get("/terminals/{terminal_id}/conversation")
async def get_terminal_conversation(terminal_id: str) -> dict:
    """Return parsed conversation turns for a terminal."""
    from agentfactor.services.analysis_service import AnalysisService
    turns = AnalysisService().get_conversation(terminal_id)
    if turns is None:
        raise HTTPException(status_code=404, detail="No analysis found for this terminal")
    return {"terminal_id": terminal_id, "turns": turns}


@app.get("/terminal-analyses/search")
async def search_terminal_analyses(q: str = "", limit: int = 20) -> list:
    """Full-text search across terminal history logs."""
    from agentfactor.services.analysis_service import AnalysisService
    return AnalysisService().search_history(query=q, limit=limit)


@app.post("/inbox", response_model=InboxMessage, status_code=status.HTTP_201_CREATED)
async def enqueue_message(
    payload: InboxCreateRequest,
    inbox: InboxService = Depends(get_inbox_service),
) -> InboxMessage:
    # Explicit API calls should not be deduplicated - user intent is clear
    result = inbox.queue_message(
        sender_id=payload.sender_id,
        receiver_id=payload.receiver_id,
        message=payload.message,
        dedupe=False,
    )
    assert result is not None  # dedupe=False guarantees a result
    return result


@app.get("/inbox/{terminal_id}", response_model=List[InboxMessage])
async def list_inbox(
    terminal_id: str,
    inbox: InboxService = Depends(get_inbox_service),
) -> List[InboxMessage]:
    return inbox.list_messages(terminal_id)


@app.post("/inbox/{terminal_id}/deliver", status_code=status.HTTP_202_ACCEPTED)
async def deliver_inbox(
    terminal_id: str,
    inbox: InboxService = Depends(get_inbox_service),
) -> None:
    inbox.deliver_pending(terminal_id)


@app.post("/flows", response_model=Flow, status_code=status.HTTP_201_CREATED)
async def register_flow(
    payload: FlowCreateRequest,
    flows: FlowService = Depends(get_flow_service),
) -> Flow:
    return flows.register_flow(
        name=payload.name,
        file_path=payload.file_path,
        schedule=payload.schedule,
        agent_profile=payload.agent_profile,
        script=payload.script,
    )


@app.get("/flows", response_model=List[Flow])
async def list_flows(
    flows: FlowService = Depends(get_flow_service),
) -> List[Flow]:
    return flows.list_flows()


@app.get("/flows/{name}", response_model=Flow)
async def get_flow(
    name: str,
    flows: FlowService = Depends(get_flow_service),
) -> Flow:
    flow = flows.get_flow(name)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found.")
    return flow


@app.post("/flows/{name}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_flow(
    name: str,
    flows: FlowService = Depends(get_flow_service),
    terminals: TerminalService = Depends(get_terminal_service),
) -> dict[str, Any]:
    flow = flows.get_flow(name)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found.")
    try:
        terminal = _execute_flow(flow, terminals)
    except ProviderInitializationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = datetime.utcnow()
    next_run = flows.compute_next_run(flow.schedule, now) if flow.enabled else None
    flows.record_run(name, last_run=now, next_run=next_run)
    return {"status": "triggered", "terminal_id": terminal.id, "next_run": next_run}


@app.post("/flows/{name}/enable", status_code=status.HTTP_202_ACCEPTED)
async def enable_flow(
    name: str,
    flows: FlowService = Depends(get_flow_service),
) -> None:
    flows.set_enabled(name, True)


@app.post("/flows/{name}/disable", status_code=status.HTTP_202_ACCEPTED)
async def disable_flow(
    name: str,
    flows: FlowService = Depends(get_flow_service),
) -> None:
    flows.set_enabled(name, False)


@app.delete("/flows/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flow(
    name: str,
    flows: FlowService = Depends(get_flow_service),
) -> None:
    flows.delete_flow(name)


@app.post("/approvals", response_model=ApprovalRequest, status_code=status.HTTP_201_CREATED)
async def request_approval(
    payload: ApprovalCreateRequest,
    approvals: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequest:
    return approvals.request_approval(
        terminal_id=payload.terminal_id,
        supervisor_id=payload.supervisor_id,
        command_text=payload.command_text,
        metadata_payload=payload.metadata_payload,
    )


@app.get("/approvals", response_model=List[ApprovalRequest])
async def list_approvals(
    status_filter: ApprovalStatus | None = None,
    approvals: ApprovalService = Depends(get_approval_service),
) -> List[ApprovalRequest]:
    return approvals.list_requests(status_filter)


@app.post("/approvals/{request_id}/approve", response_model=ApprovalRequest)
async def approve_request(
    request_id: int,
    approvals: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequest:
    return approvals.approve(request_id)


@app.post("/approvals/{request_id}/deny", response_model=ApprovalRequest)
async def deny_request(
    request_id: int,
    payload: ApprovalDecisionRequest,
    approvals: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequest:
    return approvals.deny(request_id, reason=payload.reason)


# ---------------------------------------------------------------------------
# Work items (Phase 2 — causal work graph)
# ---------------------------------------------------------------------------


@app.post("/work-items", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
async def create_work_item(
    payload: WorkItemCreateRequest,
    work: WorkService = Depends(get_work_service),
) -> WorkItemResponse:
    return work.create_work_item(payload)


class ProjectResponse(PydanticBaseModel):
    id: str
    root_directory: Optional[str] = None


class UpsertProjectRequest(PydanticBaseModel):
    root_directory: Optional[str] = None


@app.get("/projects", response_model=List[ProjectResponse])
async def list_projects() -> List[ProjectResponse]:
    """Return all known project IDs with their root directories."""
    from sqlalchemy import distinct, select
    from agentfactor.clients.database import Project as ProjectRow
    from agentfactor.services.blueprint_service import BlueprintService

    with session_scope() as db:
        work_ids = db.execute(select(distinct(WorkItemRow.project_id))).scalars().all()
        gen_ids = db.execute(select(distinct(GenerationRequestRow.project_id))).scalars().all()
        project_rows: dict[str, Optional[str]] = {
            row.id: row.root_directory
            for row in db.query(ProjectRow).all()
        }

    all_ids: set[str] = {r for r in work_ids if r}
    all_ids.update(r for r in gen_ids if r)
    all_ids.update(BlueprintService().list_project_ids())
    all_ids.update(project_rows.keys())

    return [
        ProjectResponse(id=pid, root_directory=project_rows.get(pid))
        for pid in sorted(all_ids)
    ]


@app.put("/projects/{project_id}", response_model=ProjectResponse)
async def upsert_project(project_id: str, body: UpsertProjectRequest) -> ProjectResponse:
    """Create or update a project's root directory."""
    from agentfactor.clients.database import Project as ProjectRow

    with session_scope() as db:
        row = db.query(ProjectRow).filter_by(id=project_id).first()
        if row is None:
            row = ProjectRow(id=project_id, root_directory=body.root_directory)
            db.add(row)
        else:
            row.root_directory = body.root_directory
        db.flush()
        db.refresh(row)
        return ProjectResponse(id=row.id, root_directory=row.root_directory)


@app.get("/projects/{project_id}/blueprint-file")
async def get_project_blueprint_file(project_id: str) -> dict[str, Any]:
    """Poll whether a conductor-blueprint.toml has been written to the project root directory."""
    from agentfactor.clients.database import Project as ProjectRow

    with session_scope() as db:
        project_row = db.query(ProjectRow).filter_by(id=project_id).first()
        root_dir = project_row.root_directory if project_row else None

    if not root_dir:
        return {"found": False, "complete": False, "path": None, "item_count": 0, "toml_content": None}

    blueprint_path = Path(root_dir).expanduser().resolve() / "conductor-blueprint.toml"
    if not blueprint_path.exists():
        return {"found": False, "complete": False, "path": str(blueprint_path), "item_count": 0, "toml_content": None}

    try:
        content = blueprint_path.read_text(encoding="utf-8")
    except OSError:
        return {"found": True, "complete": False, "path": str(blueprint_path), "item_count": 0, "toml_content": None}

    try:
        try:
            import tomllib as _tl
        except ImportError:
            import tomli as _tl  # type: ignore[no-redef]
        doc = _tl.loads(content)
        items = doc.get("items", [])
        complete = (
            "meta" in doc
            and len(items) > 0
            and all(
                item.get("id") and item.get("title") and item.get("category") and item.get("priority") is not None
                for item in items
            )
        )
        return {
            "found": True,
            "complete": complete,
            "path": str(blueprint_path),
            "item_count": len(items),
            "toml_content": content if complete else None,
        }
    except Exception:
        # TOML not yet fully written — agent may still be streaming
        return {"found": True, "complete": False, "path": str(blueprint_path), "item_count": 0, "toml_content": None}


@app.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str) -> None:
    """Delete a project and all its associated data (work items, edges, generation history)."""
    import shutil
    from sqlalchemy import delete as sql_delete, select
    from agentfactor.clients.database import (
        BlueprintJob as BlueprintJobRow,
        Project as ProjectRow,
        WorkEdge as WorkEdgeRow,
    )
    from agentfactor import constants

    with session_scope() as db:
        item_ids = db.execute(
            select(WorkItemRow.id).where(WorkItemRow.project_id == project_id)
        ).scalars().all()

        if item_ids:
            db.execute(
                sql_delete(WorkEdgeRow).where(
                    WorkEdgeRow.from_id.in_(item_ids) | WorkEdgeRow.to_id.in_(item_ids)
                )
            )

        db.execute(sql_delete(WorkItemRow).where(WorkItemRow.project_id == project_id))
        db.execute(sql_delete(GenerationRequestRow).where(GenerationRequestRow.project_id == project_id))
        db.execute(sql_delete(BlueprintJobRow).where(BlueprintJobRow.project_id == project_id))
        db.execute(sql_delete(ProjectRow).where(ProjectRow.id == project_id))

    # Remove blueprint files directory so the project doesn't reappear via filesystem scan
    blueprint_dir = Path(constants.BLUEPRINTS_DIR) / project_id
    if blueprint_dir.exists():
        shutil.rmtree(blueprint_dir, ignore_errors=True)


class GenerateWorkGraphRequest(PydanticBaseModel):
    project_id: str
    description: str
    constraints: str = ""
    persona: str = "conductor"
    provider: str = "claude_code"
    mcp_capable: bool = True
    base_blueprint_terminal_id: Optional[str] = None
    language: str = "en"
    root_directory: Optional[str] = None


class SubmitBlueprintRequest(PydanticBaseModel):
    toml_content: str


class ImportBlueprintRequest(PydanticBaseModel):
    selected_item_ids: Optional[List[str]] = None


def _serialize_generation_request(row: GenerationRequestRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "terminal_id": row.terminal_id,
        "project_id": row.project_id,
        "description": row.description,
        "constraints": row.constraints,
        "persona": row.persona,
        "provider": row.provider,
        "mcp_capable": row.mcp_capable,
        "base_blueprint_terminal_id": row.base_blueprint_terminal_id,
        "created_at": str(row.created_at),
    }


def _blueprint_revision_section(base_blueprint: Optional[dict]) -> str:
    if not base_blueprint:
        return ""
    title = base_blueprint.get("meta", {}).get("title") or base_blueprint.get("terminal_id")
    toml_content = base_blueprint.get("toml_content", "")
    return f"""

## Existing blueprint to revise

Use this previous blueprint as the baseline. Preserve useful work items, rewrite weak ones,
remove obsolete items, and add new items required by the current request. Do not import or
execute anything; produce a new complete blueprint.

Baseline: {title}

```toml
{toml_content}
```
"""


_LANGUAGE_NAMES: dict[str, str] = {
    "zh": "Chinese (Simplified)",
    "en": "English",
}


def _root_directory_section(root_directory: Optional[str]) -> str:
    if not root_directory:
        return ""
    return (
        f"\n\n## Project Root Directory\n"
        f"The project lives at `{root_directory}`. "
        "All `files_of_interest` paths must be relative to this root (e.g. `src/auth/login.py`, not absolute paths)."
    )


def _language_section(language: str) -> str:
    name = _LANGUAGE_NAMES.get(language, "English")
    if language == "en":
        return ""
    return (
        f"\n\n## Output Language\nWrite all human-readable text fields (title, description, "
        f"acceptance_criteria) in {name}. "
        "Keep category, proof_requirements, and files_of_interest in English — they are machine-readable identifiers."
    )


def _build_mcp_prompt(
    project_id: str,
    description: str,
    constraints: str,
    base_blueprint: Optional[dict] = None,
    language: str = "en",
    root_directory: Optional[str] = None,
) -> str:
    constraint_section = f"\n\nAdditional constraints:\n{constraints}" if constraints.strip() else ""
    revision_section = _blueprint_revision_section(base_blueprint)
    language_section = _language_section(language)
    root_section = _root_directory_section(root_directory)
    return f"""You are a senior software architect. Your sole task is to produce a work graph blueprint in TOML format for the project described below, then submit it using the `submit_blueprint` MCP tool.

## Project
{description}{constraint_section}{root_section}{revision_section}{language_section}

## Output format

You MUST call the `submit_blueprint` tool with a TOML string that follows this structure exactly (replace example values with real ones):

```toml
[meta]
schema_version = "1"
project_id     = "{project_id}"
title          = "My Project"
generated_by   = "planner"
generated_at   = "2025-01-01T00:00:00Z"

[[items]]
id           = "feat-001"
title        = "Implement login API"
description  = "Add POST /auth/login endpoint with JWT response."
category     = "feature"
priority     = 2
effort_hours = 4.0
depends_on   = []
proof_requirements  = ["git_commit", "test_pass"]
acceptance_criteria = ["Returns 200 with token on valid credentials"]
files_of_interest   = ["src/auth/login.py"]

[[items]]
id           = "feat-002"
title        = "Add unit tests for login"
description  = "Write pytest tests covering happy path and error cases."
category     = "test"
priority     = 2
effort_hours = 2.0
depends_on   = ["feat-001"]
proof_requirements  = ["test_pass"]
acceptance_criteria = ["Coverage >= 80%"]
files_of_interest   = ["tests/test_login.py"]
```

## Rules
- category must be one of: feature, bugfix, refactor, test, documentation, review, investigation
- priority is an integer 1–5 (1 = critical, 5 = low)
- effort_hours is a decimal number (e.g. 2.0, 4.5)
- All string values must be enclosed in double quotes on a single line
- depends_on references only id values defined in the same blueprint
- Features need `git_commit` + `test_pass`; docs need `git_commit`; investigations need `completion_signal`
- Break the project into 5–20 atomic work items; each completable by one agent in one session
- Call `submit_blueprint(toml_content=...)` exactly once with the full TOML string. Do not explain your work.
- If `submit_blueprint` is not in your available tools, wrap the complete TOML in sentinel markers and output ONLY that block:
  <<<CONDUCTOR_BLUEPRINT_BEGIN>>>
  [meta]
  ...all items...
  <<<CONDUCTOR_BLUEPRINT_END>>>
"""


def _build_stdout_prompt(
    project_id: str,
    description: str,
    constraints: str,
    base_blueprint: Optional[dict] = None,
    language: str = "en",
    root_directory: Optional[str] = None,
) -> str:
    constraint_section = f"\n\nAdditional constraints:\n{constraints}" if constraints.strip() else ""
    revision_section = _blueprint_revision_section(base_blueprint)
    language_section = _language_section(language)
    root_section = _root_directory_section(root_directory)
    return f"""You are a senior software architect. Your sole task is to produce a work graph blueprint in TOML format for the project described below, then deliver it using the method below.

## Project
{description}{constraint_section}{root_section}{revision_section}{language_section}

## Delivery method

Wrap the complete TOML in sentinel markers and print it to stdout (replace example values with real ones):

<<<CONDUCTOR_BLUEPRINT_BEGIN>>>
[meta]
schema_version = "1"
project_id     = "{project_id}"
title          = "My Project"
generated_by   = "planner"
generated_at   = "2025-01-01T00:00:00Z"

[[items]]
id           = "feat-001"
title        = "Implement login API"
description  = "Add POST /auth/login endpoint with JWT response."
category     = "feature"
priority     = 2
effort_hours = 4.0
depends_on   = []
proof_requirements  = ["git_commit", "test_pass"]
acceptance_criteria = ["Returns 200 with token on valid credentials"]
files_of_interest   = ["src/auth/login.py"]

[[items]]
id           = "feat-002"
title        = "Add unit tests for login"
description  = "Write pytest tests covering happy path and error cases."
category     = "test"
priority     = 2
effort_hours = 2.0
depends_on   = ["feat-001"]
proof_requirements  = ["test_pass"]
acceptance_criteria = ["Coverage >= 80%"]
files_of_interest   = ["tests/test_login.py"]
<<<CONDUCTOR_BLUEPRINT_END>>>

## Rules
- category must be one of: feature, bugfix, refactor, test, documentation, review, investigation
- priority is an integer 1–5 (1 = critical, 5 = low)
- effort_hours is a decimal number (e.g. 2.0, 4.5)
- All string values must be enclosed in double quotes on a single line
- depends_on references only id values defined in the same blueprint
- Features need `git_commit` + `test_pass`; docs need `git_commit`; investigations need `completion_signal`
- Break the project into 5–20 atomic work items; each completable by one agent in one session
- Output ONLY the sentinel-wrapped TOML block. No preamble, no explanation, nothing else.
"""


@app.post("/work-graph/generate")
async def generate_work_graph(
    req: GenerateWorkGraphRequest,
    terminals: TerminalService = Depends(get_terminal_service),
    inbox: InboxService = Depends(get_inbox_service),
) -> dict:
    """Launch a planner terminal and inject the blueprint generation prompt."""
    from agentfactor.services.blueprint_service import BlueprintService

    base_blueprint = None
    if req.base_blueprint_terminal_id:
        base_blueprint = BlueprintService().get_blueprint(req.base_blueprint_terminal_id)
        if base_blueprint is None:
            raise HTTPException(status_code=404, detail="Base blueprint not found")
        if base_blueprint.get("project_id") != req.project_id:
            raise HTTPException(status_code=400, detail="Base blueprint belongs to a different project")

        from sqlalchemy import select as sa_select
        from agentfactor.clients.database import BlueprintJob

        with session_scope() as db:
            job = db.execute(
                sa_select(BlueprintJob).where(BlueprintJob.terminal_id == req.base_blueprint_terminal_id)
            ).scalar_one_or_none()
            if job is not None:
                base_blueprint["toml_content"] = job.toml_content

    # Use MCP prompt only when the resolved profile actually has mcpServers configured.
    # req.mcp_capable only means the provider supports --mcp-config; it does not guarantee
    # the persona profile has submit_blueprint wired up.
    _use_mcp = False
    if req.mcp_capable:
        try:
            _profile = load_agent_profile(req.persona)
            _use_mcp = bool(_profile.mcpServers)
        except AgentProfileError:
            pass

    prompt = (
        _build_mcp_prompt(req.project_id, req.description, req.constraints, base_blueprint, req.language, req.root_directory)
        if _use_mcp
        else _build_stdout_prompt(req.project_id, req.description, req.constraints, base_blueprint, req.language, req.root_directory)
    )

    # Pre-generate terminal_id and persist the request row immediately so it shows up
    # in the History tab even while the terminal is still being launched.
    from agentfactor.utils.terminal import generate_terminal_id as _gen_tid
    pre_terminal_id = _gen_tid()
    with session_scope() as db:
        db.add(
            GenerationRequestRow(
                terminal_id=pre_terminal_id,
                project_id=req.project_id,
                description=req.description,
                constraints=req.constraints,
                persona=req.persona,
                provider=req.provider,
                mcp_capable=req.mcp_capable,
                base_blueprint_terminal_id=req.base_blueprint_terminal_id,
            )
        )

    try:
        terminal = terminals.create_terminal(
            provider_key=req.provider,
            role="planner",
            agent_profile=req.persona,
            terminal_id=pre_terminal_id,
        )
    except ProviderInitializationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    inbox.queue_message(
        sender_id=terminal.id,
        receiver_id=terminal.id,
        message=prompt,
        dedupe=False,
    )
    return {"terminal_id": terminal.id, "session_name": terminal.session_name}


@app.get("/projects/{project_id}/work-graph/generation-requests")
async def list_work_graph_generation_requests(
    project_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return recent Generate Work Graph form submissions for this project."""
    from sqlalchemy import select

    with session_scope() as db:
        rows = db.execute(
            select(GenerationRequestRow)
            .where(GenerationRequestRow.project_id == project_id)
            .order_by(GenerationRequestRow.created_at.desc(), GenerationRequestRow.id.desc())
            .limit(max(1, min(limit, 100)))
        ).scalars().all()
        return [_serialize_generation_request(row) for row in rows]


@app.delete("/work-graph/generation-requests/{request_id}", status_code=204)
async def delete_work_graph_generation_request(request_id: int) -> None:
    """Delete a single generation request record by id."""
    from sqlalchemy import select

    with session_scope() as db:
        row = db.execute(
            select(GenerationRequestRow).where(GenerationRequestRow.id == request_id)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Generation request not found")
        db.delete(row)


@app.get("/projects/{project_id}/work-graph/blueprints")
async def list_work_graph_blueprints(
    project_id: str,
    limit: int = 20,
) -> list[dict]:
    """Return recent generated work graph blueprints for this project."""
    from agentfactor.services.blueprint_service import BlueprintService

    return BlueprintService().list_blueprints(project_id, limit=limit)


@app.post("/work-graph/blueprint/{terminal_id}")
async def submit_blueprint(
    terminal_id: str,
    req: SubmitBlueprintRequest,
    terminals: TerminalService = Depends(get_terminal_service),
) -> dict:
    """Called by planner agent via MCP to submit a finished blueprint."""
    from agentfactor.services.blueprint_service import BlueprintParseError, BlueprintService
    from agentfactor.clients.database import Project as ProjectRow
    terminal = terminals.get_terminal(terminal_id)
    if terminal is None:
        raise HTTPException(status_code=404, detail="Terminal not found")
    project_id = _extract_blueprint_project_id(req.toml_content)
    try:
        job_id = BlueprintService().save_blueprint(terminal_id, project_id, req.toml_content)
    except BlueprintParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Write TOML to project root directory so frontend can poll the file
    with session_scope() as db:
        project_row = db.query(ProjectRow).filter_by(id=project_id).first()
        root_dir = project_row.root_directory if project_row else None
    if root_dir:
        try:
            Path(root_dir).expanduser().resolve().joinpath("conductor-blueprint.toml").write_text(
                req.toml_content, encoding="utf-8"
            )
        except OSError:
            pass  # don't fail submission if file write fails

    return {"status": "accepted", "job_id": job_id}


@app.get("/work-graph/blueprint/{terminal_id}")
async def get_blueprint(
    terminal_id: str,
    pending_ok: bool = False,
    terminals: TerminalService = Depends(get_terminal_service),
) -> dict:
    """Return the blueprint for a completed planner terminal."""
    from agentfactor.models.enums import TerminalStatus
    from agentfactor.services.blueprint_service import BlueprintService
    history: Optional[str] = None
    captured_history: Optional[str] = None
    terminal = terminals.get_terminal(terminal_id)
    if terminal is not None:
        try:
            captured_history = terminals.capture_output(terminal_id)
        except Exception:
            captured_history = None

        fatal_error = _blueprint_generation_error(captured_history)
        if fatal_error:
            from agentfactor.clients.database import Terminal as TerminalRow
            with session_scope() as db:
                row = db.get(TerminalRow, terminal_id)
                if row is not None:
                    row.status = TerminalStatus.ERROR
            return {
                "terminal_id": terminal_id,
                "status": "error",
                "ready": False,
                "error": fatal_error,
            }

        # Only attempt stdout TOML extraction after the terminal has finished.
        # While RUNNING the history only contains the inbox prompt template
        # (with placeholder values like <float estimate>) which causes false
        # parse failures.
        if terminal.status in (TerminalStatus.COMPLETED, TerminalStatus.ERROR):
            history = captured_history
    result = BlueprintService().get_blueprint(terminal_id, terminal_history=history)
    if result is None:
        if pending_ok:
            return {
                "terminal_id": terminal_id,
                "status": "pending",
                "ready": False,
                "detail": "Blueprint not found - terminal may still be running",
            }
        raise HTTPException(status_code=404, detail="Blueprint not found — terminal may still be running")

    # Write TOML to project root directory (covers stdout path; MCP path is handled in submit_blueprint)
    if not result.get("error"):
        from agentfactor.clients.database import BlueprintJob as _BPJob, Project as _ProjectRow
        from sqlalchemy import select as _sa_select
        _project_id = result.get("project_id", "")
        if _project_id:
            with session_scope() as _db:
                _project = _db.query(_ProjectRow).filter_by(id=_project_id).first()
                _root_dir = _project.root_directory if _project else None
                if _root_dir:
                    _job = _db.execute(
                        _sa_select(_BPJob).where(_BPJob.terminal_id == terminal_id)
                    ).scalar_one_or_none()
                    if _job:
                        try:
                            Path(_root_dir).expanduser().resolve().joinpath(
                                "conductor-blueprint.toml"
                            ).write_text(_job.toml_content, encoding="utf-8")
                        except OSError:
                            pass

    return result


@app.post("/work-graph/blueprint/{terminal_id}/import", response_model=List[WorkItemResponse])
async def import_blueprint(
    terminal_id: str,
    req: ImportBlueprintRequest,
) -> List[WorkItemResponse]:
    """Import selected work items from a blueprint into the work graph."""
    from agentfactor.services.blueprint_service import BlueprintService
    blueprint = BlueprintService().get_blueprint(terminal_id)
    if blueprint is None:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    if "error" in blueprint:
        raise HTTPException(status_code=422, detail=blueprint["error"])
    try:
        return BlueprintService().import_blueprint(
            terminal_id,
            req.selected_item_ids,
            blueprint["project_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _extract_blueprint_project_id(toml_content: str) -> str:
    try:
        import tomllib as _tl
    except ImportError:
        import tomli as _tl  # type: ignore[no-redef]
    try:
        doc = _tl.loads(toml_content)
        return doc.get("meta", {}).get("project_id", "default") or "default"
    except Exception:
        return "default"


@app.get("/projects/{project_id}/work-items", response_model=List[WorkItemResponse])
async def list_work_items(
    project_id: str,
    item_status: Optional[str] = None,
    work: WorkService = Depends(get_work_service),
) -> List[WorkItemResponse]:
    from agentfactor.models.enums import WorkItemStatus as WIS
    status_filter = WIS(item_status) if item_status else None
    return work.list_work_items(project_id, status=status_filter)


@app.get("/work-items/{item_id}", response_model=WorkItemResponse)
async def get_work_item(
    item_id: str,
    work: WorkService = Depends(get_work_service),
) -> WorkItemResponse:
    item = work.get_work_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    return item


@app.patch("/work-items/{item_id}", response_model=WorkItemResponse)
async def update_work_item(
    item_id: str,
    payload: WorkItemUpdateRequest,
    work: WorkService = Depends(get_work_service),
) -> WorkItemResponse:
    try:
        item = work.update_work_item(item_id, payload)
    except ProofRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if item is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    return item


@app.delete("/work-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_item(
    item_id: str,
    work: WorkService = Depends(get_work_service),
) -> None:
    if not work.delete_work_item(item_id):
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")


@app.post("/work-items/{item_id}/edges", response_model=WorkEdgeResponse, status_code=status.HTTP_201_CREATED)
async def create_work_edge(
    item_id: str,
    payload: WorkEdgeCreateRequest,
    work: WorkService = Depends(get_work_service),
) -> WorkEdgeResponse:
    if work.get_work_item(item_id) is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    return work.create_edge(payload)


@app.delete("/work-edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_edge(
    edge_id: int,
    work: WorkService = Depends(get_work_service),
) -> None:
    if not work.delete_edge(edge_id):
        raise HTTPException(status_code=404, detail=f"Work edge '{edge_id}' not found.")


@app.get("/projects/{project_id}/work-graph", response_model=WorkGraphResponse)
async def get_work_graph(
    project_id: str,
    work: WorkService = Depends(get_work_service),
) -> WorkGraphResponse:
    return work.get_work_graph(project_id)


@app.get("/work-items/{item_id}/proof-windows")
async def list_proof_windows(
    item_id: str,
    work: WorkService = Depends(get_work_service),
) -> List[dict]:
    if work.get_work_item(item_id) is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    return work.list_proof_windows(item_id)


# ---------------------------------------------------------------------------
# Agent workflow endpoints (claim / complete / block / available / assign)
# ---------------------------------------------------------------------------


class ClaimWorkItemRequest(PydanticBaseModel):
    terminal_id: str


class CompleteWorkItemRequest(PydanticBaseModel):
    terminal_id: str
    summary: str
    commit_hash: Optional[str] = None


class BlockWorkItemRequest(PydanticBaseModel):
    terminal_id: str
    reason: str


class AssignWorkItemRequest(PydanticBaseModel):
    terminal_id: str


@app.post("/work-items/{item_id}/claim", response_model=WorkItemResponse)
async def claim_work_item(
    item_id: str,
    req: ClaimWorkItemRequest,
    work: WorkService = Depends(get_work_service),
) -> WorkItemResponse:
    """Atomically claim an unclaimed READY work item for a terminal."""
    try:
        return work.claim_work_item(item_id, req.terminal_id)
    except ValueError as exc:
        detail = str(exc)
        code = 409 if "already claimed" in detail else 404 if "not found" in detail else 422
        raise HTTPException(status_code=code, detail=detail)


@app.post("/work-items/{item_id}/complete", response_model=WorkItemResponse)
async def complete_work_item(
    item_id: str,
    req: CompleteWorkItemRequest,
    work: WorkService = Depends(get_work_service),
) -> WorkItemResponse:
    """Transition a work item to needs_verification and open a proof window."""
    item = work.get_work_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    note = f"\n\n**Completion summary** (by {req.terminal_id}): {req.summary}"
    if req.commit_hash:
        note += f"\nCommit: {req.commit_hash}"
    try:
        result = work.update_work_item(
            item_id,
            WorkItemUpdateRequest(
                status=WorkItemStatus.NEEDS_VERIFICATION,
                description=(item.description or "") + note,
            ),
        )
    except ProofRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    return result


@app.post("/work-items/{item_id}/block", response_model=WorkItemResponse)
async def block_work_item(
    item_id: str,
    req: BlockWorkItemRequest,
    work: WorkService = Depends(get_work_service),
) -> WorkItemResponse:
    """Mark a work item as blocked and record the reason."""
    item = work.get_work_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    note = f"\n\n**Blocked** (by {req.terminal_id}): {req.reason}"
    result = work.update_work_item(
        item_id,
        WorkItemUpdateRequest(
            status=WorkItemStatus.BLOCKED,
            description=(item.description or "") + note,
        ),
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    return result


@app.post("/work-items/{item_id}/assign", response_model=WorkItemResponse)
async def assign_work_item(
    item_id: str,
    req: AssignWorkItemRequest,
    work: WorkService = Depends(get_work_service),
) -> WorkItemResponse:
    """Directly assign a work item to a terminal (conductor use — skips claim check)."""
    result = work.update_work_item(
        item_id,
        WorkItemUpdateRequest(
            owner_terminal_id=req.terminal_id,
            status=WorkItemStatus.IN_PROGRESS,
        ),
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Work item '{item_id}' not found.")
    return result


@app.get("/projects/{project_id}/work-items/available", response_model=List[WorkItemResponse])
async def list_available_work_items(
    project_id: str,
    work: WorkService = Depends(get_work_service),
) -> List[WorkItemResponse]:
    """Return READY unclaimed items whose dependencies are all DONE."""
    return work.list_available_work_items(project_id)


@app.get("/work-items/{item_id}/verifier-runs", response_model=List[VerifierRunResponse])
async def list_work_item_verifier_runs(
    item_id: str,
    reviews: LLMReviewService = Depends(get_llm_review_service),
) -> List[VerifierRunResponse]:
    """Return verifier attempts for a work item, newest first."""
    return reviews.list_verifier_runs(item_id)


@app.get("/terminals/{terminal_id}/work-items", response_model=List[WorkItemResponse])
async def list_terminal_work_items(
    terminal_id: str,
    work: WorkService = Depends(get_work_service),
) -> List[WorkItemResponse]:
    """Return all work items owned by a terminal."""
    return work.list_by_owner(terminal_id)


# ---------------------------------------------------------------------------
# Context packs (Phase 3 — semantic context store + context integrity system)
# ---------------------------------------------------------------------------


@app.post("/terminals/{terminal_id}/context-pack")
async def build_context_pack(
    terminal_id: str,
    payload: ContextPackRequest,
    packs: ContextPackService = Depends(get_context_pack_service),
) -> dict[str, Any]:
    """Build and return a fresh context pack for a terminal."""
    return packs.build_context_pack(
        terminal_id=terminal_id,
        query=payload.query,
        token_budget=payload.token_budget,
    )


@app.get("/terminals/{terminal_id}/context-pack/latest")
async def get_latest_context_pack(
    terminal_id: str,
    packs: ContextPackService = Depends(get_context_pack_service),
) -> dict[str, Any]:
    """Return the most recently stored context pack for a terminal."""
    pack = packs.get_latest_pack(terminal_id)
    if pack is None:
        raise HTTPException(
            status_code=404,
            detail=f"No context pack found for terminal '{terminal_id}'.",
        )
    return pack


@app.post("/terminals/{terminal_id}/context-pack/differential")
async def build_differential_pack(
    terminal_id: str,
    payload: DifferentialPackRequest,
    packs: ContextPackService = Depends(get_context_pack_service),
) -> dict[str, Any]:
    """Build a differential context pack relative to a prior base pack."""
    return packs.build_differential_pack(
        terminal_id=terminal_id,
        base_pack_id=payload.base_pack_id,
        query=payload.query,
    )


# ---------------------------------------------------------------------------
# Topology Engine + Capability Registry (Phase 4)
# ---------------------------------------------------------------------------


@app.get("/topology/proposals", response_model=List[TopologyProposalResponse])
async def list_topology_proposals(
    status: Optional[str] = None,
) -> List[TopologyProposalResponse]:
    """List topology proposals, optionally filtered by status (pending/accepted/rejected)."""
    from sqlalchemy import select as sa_select
    from agentfactor.clients.database import TopologyProposal, session_scope
    import json

    with session_scope() as db:
        q = sa_select(TopologyProposal).order_by(TopologyProposal.created_at.desc())
        if status:
            q = q.where(TopologyProposal.status == status)
        rows = db.execute(q).scalars().all()
        return [
            TopologyProposalResponse(
                id=r.id,
                terminal_id=r.terminal_id,
                proposal_type=r.proposal_type,
                reason=r.reason,
                suggested_provider=r.suggested_provider,
                suggested_persona=r.suggested_persona,
                metrics_snapshot=_safe_json(r.metrics_snapshot),
                status=r.status,
                created_at=str(r.created_at),
                decided_at=str(r.decided_at) if r.decided_at else None,
            )
            for r in rows
        ]


@app.post("/topology/proposals/{proposal_id}/accept")
async def accept_topology_proposal(proposal_id: str) -> dict[str, str]:
    """Accept a pending topology proposal."""
    return _decide_proposal(proposal_id, "accepted")


@app.post("/topology/proposals/{proposal_id}/reject")
async def reject_topology_proposal(proposal_id: str) -> dict[str, str]:
    """Reject a pending topology proposal."""
    return _decide_proposal(proposal_id, "rejected")


def _decide_proposal(proposal_id: str, decision: str) -> dict[str, str]:
    from agentfactor.clients.database import TopologyProposal, session_scope
    from datetime import datetime as _dt

    with session_scope() as db:
        row = db.get(TopologyProposal, proposal_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found.")
        if row.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Proposal '{proposal_id}' already decided ({row.status}).",
            )
        row.status = decision
        row.decided_at = _dt.utcnow().isoformat()
    return {"proposal_id": proposal_id, "status": decision}


@app.get("/capability-estimates", response_model=List[CapabilityEstimateResponse])
async def list_capability_estimates(
    registry: CapabilityRegistry = Depends(get_capability_registry),
) -> List[CapabilityEstimateResponse]:
    """Return all stored capability estimates."""
    return [CapabilityEstimateResponse(**e) for e in registry.list_estimates()]


@app.get(
    "/capability-estimates/{provider}/{persona}/{task_type}",
    response_model=CapabilityEstimateResponse,
)
async def get_capability_estimate(
    provider: str,
    persona: str,
    task_type: str,
    registry: CapabilityRegistry = Depends(get_capability_registry),
) -> CapabilityEstimateResponse:
    """Return the Beta posterior estimate for a specific (provider, persona, task_type) triple."""
    return CapabilityEstimateResponse(**registry.get_estimate(provider, persona, task_type))


def _safe_json(value: Any) -> Any:
    """Parse a JSON string to a dict/list, or return as-is if already structured."""
    if isinstance(value, str):
        try:
            import json
            return json.loads(value)
        except Exception:
            return value
    return value


# ---------------------------------------------------------------------------
# Compaction Service (Phase 5)
# ---------------------------------------------------------------------------


@app.get("/compaction/history")
async def list_compaction_history(
    terminal_id: Optional[str] = None,
    compaction: CompactionService = Depends(get_compaction_service),
) -> List[dict]:
    """List snapshot history, newest first. Optionally filter by terminal_id."""
    return compaction.get_compaction_history(terminal_id=terminal_id)


@app.get("/compaction/diff/{snapshot_id_a}/{snapshot_id_b}")
async def diff_snapshots(
    snapshot_id_a: str,
    snapshot_id_b: str,
    compaction: CompactionService = Depends(get_compaction_service),
) -> dict:
    """Return a structured diff between two snapshots."""
    try:
        return compaction.diff_snapshots(snapshot_id_a, snapshot_id_b)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/terminals/{terminal_id}/compaction/trigger")
async def trigger_compaction(
    terminal_id: str,
    compaction: CompactionService = Depends(get_compaction_service),
) -> dict:
    """Manually trigger compaction for a terminal; returns the new snapshot or 204 if nothing to do."""
    result = compaction.compact_terminal(terminal_id)
    if result is None:
        raise HTTPException(
            status_code=204,
            detail=f"No new events to compact for terminal '{terminal_id}'.",
        )
    return result


@app.get("/terminals/{terminal_id}/compaction/latest")
async def get_latest_snapshot(
    terminal_id: str,
    compaction: CompactionService = Depends(get_compaction_service),
) -> dict:
    """Return the most recent snapshot for a terminal."""
    history = compaction.get_compaction_history(terminal_id=terminal_id)
    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"No snapshots found for terminal '{terminal_id}'.",
        )
    return history[0]
