from agentfactor.clients.database import (
    ApprovalRequest as ApprovalORM,
    BlueprintJob,
    InboxMessage as InboxORM,
    Terminal as TerminalORM,
    TerminalMetrics as TerminalMetricsORM,
    WorkItem as WorkItemORM,
    session_scope,
)
from agentfactor import constants
from agentfactor.api import main as api_main
from agentfactor.models.enums import ApprovalStatus, TerminalStatus, WorkItemStatus, WorkItemType


def test_admin_route_serves_fantastic_index(api_client, monkeypatch, tmp_path):
    admin_dist = tmp_path / "admin-dist"
    admin_dist.mkdir()
    (admin_dist / "index.html").write_text("<div id=\"app\">AgentFactor Admin</div>", encoding="utf-8")

    monkeypatch.setattr(api_main, "ADMIN_DIST_DIR", admin_dist)
    response = api_client.get("/admin")
    assert response.status_code == 200
    assert "AgentFactor Admin" in response.text


def test_root_redirects_to_admin(api_client):
    response = api_client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin"


def test_admin_route_reports_unbuilt_frontend(api_client, monkeypatch, tmp_path):
    monkeypatch.setattr(api_main, "ADMIN_DIST_DIR", tmp_path / "missing-admin-dist")
    response = api_client.get("/admin")
    assert response.status_code == 404
    assert "Fantastic-admin dashboard has not been built" in response.json()["detail"]


def test_persona_and_provider_catalogs(api_client):
    personas_resp = api_client.get("/personas")
    assert personas_resp.status_code == 200
    personas = personas_resp.json()
    deepseek_tester = next(item for item in personas if item["name"] == "deepseek_tester")
    assert deepseek_tester["default_provider"] == "deepseek"
    assert "qa" in deepseek_tester["tags"]

    providers_resp = api_client.get("/providers")
    assert providers_resp.status_code == 200
    providers = providers_resp.json()
    assert any(item["key"] == "claude_code" for item in providers)
    assert any(item["key"] == "deepseek" and item["binary"] == "deepcode" for item in providers)


def test_directory_browser_lists_child_directories(api_client, tmp_path):
    child = tmp_path / "workspace"
    child.mkdir()
    (tmp_path / "file.txt").write_text("not a directory", encoding="utf-8")

    response = api_client.get("/filesystem/directories", params={"path": str(tmp_path)})
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == str(tmp_path.resolve())
    assert data["parent"] == str(tmp_path.resolve().parent)
    assert any(item["path"] == str(child.resolve()) for item in data["children"])
    assert all(not item["path"].endswith("file.txt") for item in data["children"])


def test_dashboard_state_snapshot(api_client):
    response = api_client.get("/dashboard/state")
    assert response.status_code == 200
    data = response.json()
    assert data["health"]["status"] == "ok"
    assert data["health"]["terminals"]["total"] == 0
    assert data["sessions"] == []
    assert data["pending_prompt_count"] == 0
    assert data["prompt_items"] == []
    assert data["terminal_alerts"] == []
    assert data["pending_approvals"] == []
    assert data["approvals"] == []
    assert data["approvals_summary"] == {
        "pending": 0,
        "approved": 0,
        "denied": 0,
        "total": 0,
    }
    assert any(provider["key"] == "codex" for provider in data["providers"])


def test_projects_endpoint_lists_work_item_projects(api_client):
    empty = api_client.get("/projects")
    assert empty.status_code == 200
    assert empty.json() == []

    with session_scope() as db:
        db.add(
            WorkItemORM(
                id="work_test_project",
                project_id="default",
                title="Bootstrap dashboard",
                description="Initial frontend work",
                type=WorkItemType.FEATURE,
                status=WorkItemStatus.READY,
            )
        )

    response = api_client.get("/projects")
    assert response.status_code == 200
    assert response.json() == [{"id": "default", "root_directory": None}]


def test_work_item_patch_can_clear_owner_and_proof_requirements(api_client):
    created = api_client.post(
        "/work-items",
        json={
            "project_id": "qa-edit",
            "title": "Editable item",
            "description": "Initial description",
            "owner_terminal_id": "term-owner",
            "proof_requirements": ["git_commit", "test_pass"],
            "acceptance_criteria": ["Initial criterion"],
            "files_of_interest": ["src/app.py"],
        },
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    response = api_client.patch(
        f"/work-items/{item_id}",
        json={
            "owner_terminal_id": None,
            "proof_requirements": None,
            "acceptance_criteria": ["Updated criterion"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_terminal_id"] is None
    assert payload["proof_requirements"] is None
    assert payload["acceptance_criteria"] == ["Updated criterion"]


def _sample_blueprint(project_id: str = "qa-smart") -> str:
    return f"""
[meta]
schema_version = "1"
project_id = "{project_id}"
title = "Smart graph QA"
generated_by = "planner"
generated_at = "2026-05-15T10:00:00Z"

[[items]]
id = "setup"
title = "Set up foundation"
description = "Prepare the project skeleton."
category = "feature"
priority = 2
effort_hours = 3.0
depends_on = []
proof_requirements = ["git_commit", "test_pass"]
acceptance_criteria = ["Skeleton builds"]
files_of_interest = ["src/app.py"]

[[items]]
id = "ui"
title = "Build dashboard UI"
description = "Create the review workflow."
category = "feature"
priority = 3
effort_hours = 7.0
depends_on = ["setup"]
proof_requirements = ["git_commit", "test_pass"]
acceptance_criteria = ["Review modal imports selected items"]
files_of_interest = ["frontend_fantastic/apps/core/src/views/work-graph.vue"]
""".strip()


def test_projects_endpoint_lists_blueprint_only_projects(api_client):
    with session_scope() as db:
        db.add(
            BlueprintJob(
                terminal_id="term_blueprint_project",
                project_id="blueprint-db",
                toml_content=_sample_blueprint("blueprint-db"),
                status="ready",
            )
        )

    file_project_dir = constants.BLUEPRINTS_DIR / "blueprint-file"
    file_project_dir.mkdir(parents=True)
    (file_project_dir / "term-file.toml").write_text(
        _sample_blueprint("blueprint-file"),
        encoding="utf-8",
    )

    response = api_client.get("/projects")
    assert response.status_code == 200
    assert response.json() == [
        {"id": "blueprint-db", "root_directory": None},
        {"id": "blueprint-file", "root_directory": None},
    ]


def test_smart_work_graph_generation_queues_planner_prompt(api_client):
    response = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-smart",
            "description": "Build a dashboard planning workflow.",
            "constraints": "Keep import human-reviewed.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["terminal_id"]
    assert data["session_name"]

    with session_scope() as db:
        prompt = db.query(InboxORM).filter(InboxORM.receiver_id == data["terminal_id"]).one()
    assert "submit_blueprint" in prompt.message
    assert "Build a dashboard planning workflow." in prompt.message
    assert "qa-smart" in prompt.message


def test_smart_work_graph_generation_saves_request_history(api_client):
    response = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-request-history",
            "description": "Build a workflow request history picker.",
            "constraints": "Keep the original text editable.",
            "persona": "conductor",
            "provider": "codex",
            "mcp_capable": False,
            "base_blueprint_terminal_id": None,
        },
    )
    assert response.status_code == 200
    terminal_id = response.json()["terminal_id"]

    history = api_client.get("/projects/qa-request-history/work-graph/generation-requests")
    assert history.status_code == 200
    data = history.json()
    assert data[0]["terminal_id"] == terminal_id
    assert data[0]["description"] == "Build a workflow request history picker."
    assert data[0]["constraints"] == "Keep the original text editable."
    assert data[0]["provider"] == "codex"
    assert data[0]["mcp_capable"] is False

    projects = api_client.get("/projects")
    assert any(p["id"] == "qa-request-history" for p in projects.json())


def test_blueprint_submit_read_and_import_creates_items_and_edges(api_client):
    terminal = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-smart",
            "description": "Generate a two-item graph.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
        },
    ).json()
    terminal_id = terminal["terminal_id"]

    submit = api_client.post(
        f"/work-graph/blueprint/{terminal_id}",
        json={"toml_content": _sample_blueprint()},
    )
    assert submit.status_code == 200
    assert submit.json()["status"] == "accepted"

    blueprint = api_client.get(f"/work-graph/blueprint/{terminal_id}")
    assert blueprint.status_code == 200
    data = blueprint.json()
    assert data["terminal_id"] == terminal_id
    assert data["project_id"] == "qa-smart"
    assert [item["id"] for item in data["items"]] == ["setup", "ui"]

    imported = api_client.post(f"/work-graph/blueprint/{terminal_id}/import", json={})
    assert imported.status_code == 200
    imported_items = imported.json()
    assert {item["title"] for item in imported_items} == {"Set up foundation", "Build dashboard UI"}
    assert all(item["project_id"] == "qa-smart" for item in imported_items)
    assert next(item for item in imported_items if item["title"] == "Build dashboard UI")["complexity"] == 3

    graph = api_client.get("/projects/qa-smart/work-graph")
    assert graph.status_code == 200
    graph_data = graph.json()
    ids_by_title = {item["title"]: item["id"] for item in graph_data["work_items"]}
    assert {
        "from_id": ids_by_title["Set up foundation"],
        "to_id": ids_by_title["Build dashboard UI"],
        "type": "blocks",
    } in [
        {"from_id": edge["from_id"], "to_id": edge["to_id"], "type": edge["type"]}
        for edge in graph_data["edges"]
    ]
    assert graph_data["critical_path"] == [
        ids_by_title["Set up foundation"],
        ids_by_title["Build dashboard UI"],
    ]

    blueprint_path = constants.BLUEPRINTS_DIR / "qa-smart" / f"{terminal_id}.toml"
    assert blueprint_path.exists()
    assert "Smart graph QA" in blueprint_path.read_text(encoding="utf-8")

    history = api_client.get("/projects/qa-smart/work-graph/blueprints")
    assert history.status_code == 200
    history_data = history.json()
    assert history_data[0]["terminal_id"] == terminal_id
    assert history_data[0]["toml_content"].startswith("[meta]")


def test_blueprint_history_reads_toml_files_without_db_job(api_client):
    blueprint_dir = constants.BLUEPRINTS_DIR / "qa-file-history"
    blueprint_dir.mkdir(parents=True)
    (blueprint_dir / "term-file-history.toml").write_text(
        _sample_blueprint("qa-file-history"),
        encoding="utf-8",
    )

    history = api_client.get("/projects/qa-file-history/work-graph/blueprints")
    assert history.status_code == 200
    history_data = history.json()
    assert history_data[0]["terminal_id"] == "term-file-history"
    assert history_data[0]["project_id"] == "qa-file-history"
    assert history_data[0]["toml_content"].startswith("[meta]")


def test_smart_work_graph_generation_can_revise_previous_blueprint(api_client):
    first = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-revision",
            "description": "Initial graph.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
        },
    ).json()
    api_client.post(
        f"/work-graph/blueprint/{first['terminal_id']}",
        json={"toml_content": _sample_blueprint("qa-revision")},
    )

    response = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-revision",
            "description": "Add release validation and simplify setup.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
            "base_blueprint_terminal_id": first["terminal_id"],
        },
    )
    assert response.status_code == 200
    revised_terminal_id = response.json()["terminal_id"]

    with session_scope() as db:
        prompt = db.query(InboxORM).filter(InboxORM.receiver_id == revised_terminal_id).one()
    assert "Existing blueprint to revise" in prompt.message
    assert "Smart graph QA" in prompt.message
    assert "Add release validation" in prompt.message


def test_smart_work_graph_generation_rejects_cross_project_revision(api_client):
    first = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-source",
            "description": "Initial graph.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
        },
    ).json()
    api_client.post(
        f"/work-graph/blueprint/{first['terminal_id']}",
        json={"toml_content": _sample_blueprint("qa-source")},
    )

    response = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-target",
            "description": "Try cross-project revision.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
            "base_blueprint_terminal_id": first["terminal_id"],
        },
    )
    assert response.status_code == 400


def test_blueprint_validation_rejects_unknown_dependency(api_client):
    terminal = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-smart-invalid",
            "description": "Generate an invalid graph.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
        },
    ).json()
    invalid = _sample_blueprint("qa-smart-invalid").replace('depends_on = ["setup"]', 'depends_on = ["missing"]')

    response = api_client.post(
        f"/work-graph/blueprint/{terminal['terminal_id']}",
        json={"toml_content": invalid},
    )
    assert response.status_code == 422
    assert "unknown id" in response.json()["detail"]

    graph = api_client.get("/projects/qa-smart-invalid/work-graph")
    assert graph.status_code == 200
    assert graph.json()["work_items"] == []


def test_blueprint_partial_import_skips_dangling_dependency_edges(api_client):
    terminal = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-smart-partial",
            "description": "Generate a partial graph.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
        },
    ).json()
    terminal_id = terminal["terminal_id"]
    api_client.post(
        f"/work-graph/blueprint/{terminal_id}",
        json={"toml_content": _sample_blueprint("qa-smart-partial")},
    )

    imported = api_client.post(
        f"/work-graph/blueprint/{terminal_id}/import",
        json={"selected_item_ids": ["ui"]},
    )
    assert imported.status_code == 200
    assert [item["title"] for item in imported.json()] == ["Build dashboard UI"]

    graph = api_client.get("/projects/qa-smart-partial/work-graph").json()
    assert [item["title"] for item in graph["work_items"]] == ["Build dashboard UI"]
    assert graph["edges"] == []


def test_stdout_path_queues_print_prompt_not_mcp(api_client):
    """mcp_capable=False → prompt instructs agent to print TOML, not call submit_blueprint."""
    response = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-stdout",
            "description": "Build a CLI tool.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": False,
        },
    )
    assert response.status_code == 200
    terminal_id = response.json()["terminal_id"]

    with session_scope() as db:
        prompt = db.query(InboxORM).filter(InboxORM.receiver_id == terminal_id).one()

    assert "submit_blueprint" not in prompt.message
    assert "sentinel-wrapped TOML block" in prompt.message
    assert "<<<CONDUCTOR_BLUEPRINT_BEGIN>>>" in prompt.message
    assert "<<<CONDUCTOR_BLUEPRINT_END>>>" in prompt.message
    assert "qa-stdout" in prompt.message


def test_blueprint_poll_reports_claude_auth_error(api_client, terminal_service, fake_tmux):
    response = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-auth-error",
            "description": "Generate a graph.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": True,
        },
    )
    assert response.status_code == 200
    terminal_id = response.json()["terminal_id"]
    terminal = terminal_service.get_terminal(terminal_id)
    assert terminal is not None

    fake_tmux.append_history(
        terminal.session_name,
        terminal.window_name,
        "Please run /login · API Error: 401 Invalid authentication credentials",
    )

    poll = api_client.get(
        f"/work-graph/blueprint/{terminal_id}",
        params={"pending_ok": True},
    )

    assert poll.status_code == 200
    payload = poll.json()
    assert payload["status"] == "error"
    assert payload["ready"] is False
    assert "not authenticated" in payload["error"]

    with session_scope() as db:
        row = db.get(TerminalORM, terminal_id)
        assert row.status == TerminalStatus.ERROR


def test_stdout_fallback_uses_last_toml_block(api_client):
    """stdout fallback must pick the last ```toml``` block — the first is the prompt example."""
    terminal = api_client.post(
        "/work-graph/generate",
        json={
            "project_id": "qa-last-block",
            "description": "Test last-block extraction.",
            "persona": "conductor",
            "provider": "claude_code",
            "mcp_capable": False,
        },
    ).json()
    terminal_id = terminal["terminal_id"]

    # Build a synthetic pane history: prompt example block first, real blueprint last
    prompt_example = """```toml\n[meta]\nschema_version = "1"\nproject_id = "qa-last-block"\ntitle = "<project title>"\n[[items]]\nid = "<unique-slug>"\ntitle = "<short title>"\ndepends_on = []\nproof_requirements = []\nacceptance_criteria = []\n```"""
    real_blueprint = _sample_blueprint("qa-last-block")
    fake_history = f"[INBOX:x] You are a planner...\n{prompt_example}\n\n⏺\n```toml\n{real_blueprint}\n```"

    # Simulate get_blueprint with stdout fallback
    from agentfactor.services.blueprint_service import BlueprintService
    result = BlueprintService().get_blueprint(terminal_id, terminal_history=fake_history)
    assert result is not None
    assert "error" not in result
    assert result["project_id"] == "qa-last-block"
    assert result["items"][0]["id"] == "setup"


def test_dashboard_state_reports_prompt_and_approval_counts(api_client):
    supervisor_resp = api_client.post(
        "/sessions",
        json={
            "provider": "claude_code",
            "role": "supervisor",
            "agent_profile": "conductor",
        },
    )
    supervisor = supervisor_resp.json()
    worker_resp = api_client.post(
        f"/sessions/{supervisor['session_name']}/terminals",
        json={
            "provider": "claude_code",
            "role": "worker",
            "agent_profile": "developer",
        },
    )
    worker = worker_resp.json()
    api_client.post(
        "/inbox",
        json={
            "sender_id": worker["id"],
            "receiver_id": supervisor["id"],
            "message": f"[PROMPT] worker needs input\nRespond via: acd send {worker['id']} --message \"1\"",
        },
    )
    api_client.post(
        "/approvals",
        json={
            "terminal_id": worker["id"],
            "supervisor_id": supervisor["id"],
            "command_text": "sudo whoami",
        },
    )

    data = api_client.get("/dashboard/state").json()
    assert data["health"]["terminals"]["total"] == 2
    assert data["pending_prompt_count"] == 1
    assert data["prompt_items"][0]["target_id"] == worker["id"]
    assert data["prompt_items"][0]["target"]["label"].startswith("worker-")
    assert data["approvals_summary"]["pending"] == 1
    assert data["pending_approvals"][0]["risk_hints"] == ["sudo"]


def test_dashboard_state_includes_terminal_metadata(api_client):
    response = api_client.post(
        "/sessions",
        json={
            "provider": "claude_code",
            "role": "supervisor",
            "agent_profile": "conductor",
        },
    )
    assert response.status_code == 201
    terminal = response.json()

    dashboard = api_client.get("/dashboard/state")
    assert dashboard.status_code == 200
    session = dashboard.json()["sessions"][0]
    assert session["name"] == terminal["session_name"]
    assert session["terminals"][0]["id"] == terminal["id"]


def test_dashboard_state_reports_idle_terminal_alert(api_client):
    response = api_client.post(
        "/sessions",
        json={
            "provider": "claude_code",
            "role": "supervisor",
            "agent_profile": "conductor",
        },
    )
    assert response.status_code == 201
    terminal = response.json()

    with session_scope() as db:
        db.add(
            TerminalMetricsORM(
                terminal_id=terminal["id"],
                output_velocity_tpm=0.0,
                error_density=0.0,
                idle_streak_minutes=115.0,
                signal_counts="{}",
            )
        )

    dashboard = api_client.get("/dashboard/state")
    assert dashboard.status_code == 200
    alerts = dashboard.json()["terminal_alerts"]
    assert alerts == [
        {
            "id": f"idle:{terminal['id']}",
            "terminal_id": terminal["id"],
            "session_name": terminal["session_name"],
            "window_name": terminal["window_name"],
            "severity": "warning",
            "kind": "idle",
            "message": "Terminal has been idle for 115.0 minutes; inspect it or close/restart the session.",
            "idle_streak_minutes": 115.0,
        }
    ]


def test_dashboard_state_approval_payload_shows_risk_hints(api_client):
    supervisor_resp = api_client.post(
        "/sessions",
        json={
            "provider": "claude_code",
            "role": "supervisor",
            "agent_profile": "conductor",
        },
    )
    supervisor = supervisor_resp.json()
    worker_resp = api_client.post(
        f"/sessions/{supervisor['session_name']}/terminals",
        json={
            "provider": "claude_code",
            "role": "worker",
            "agent_profile": "developer",
        },
    )
    worker = worker_resp.json()
    approval_resp = api_client.post(
        "/approvals",
        json={
            "terminal_id": worker["id"],
            "supervisor_id": supervisor["id"],
            "command_text": "git reset --hard HEAD && rm -rf build",
            "metadata_payload": "cleanup request",
        },
    )
    assert approval_resp.status_code == 201

    dashboard = api_client.get("/dashboard/state")
    assert dashboard.status_code == 200
    approval = dashboard.json()["approvals"][0]
    assert approval["risk_hints"] == ["rm -rf", "git reset --hard"]
    assert approval["metadata_payload"] == "cleanup request"


def test_health_reports_terminal_counts(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["terminals"]["total"] == 0


def test_api_session_and_approval_flow(api_client, provider_manager):
    response = api_client.post(
        "/sessions",
        json={
            "provider": "claude_code",
            "role": "supervisor",
            "agent_profile": "conductor",
        },
    )
    assert response.status_code == 201
    conductor = response.json()
    session_name = conductor["session_name"]
    supervisor_id = conductor["id"]

    worker_resp = api_client.post(
        f"/sessions/{session_name}/terminals",
        json={
            "provider": "claude_code",
            "role": "worker",
            "agent_profile": "developer",
        },
    )
    assert worker_resp.status_code == 201
    worker = worker_resp.json()
    worker_id = worker["id"]

    send_resp = api_client.post(
        f"/terminals/{worker_id}/input",
        json={"message": "echo cli", "requires_approval": False},
    )
    assert send_resp.json()["status"] == "sent"

    output = api_client.get(f"/terminals/{worker_id}/output", params={"mode": "last"}).json()[
        "output"
    ]
    assert "echo cli" in output

    approval_resp = api_client.post(
        f"/terminals/{worker_id}/input",
        json={
            "message": "rm -rf /tmp",
            "requires_approval": True,
            "supervisor_id": supervisor_id,
            "metadata_payload": "dangerous",
        },
    )
    data = approval_resp.json()
    assert data["status"] == "queued_for_approval"
    approval_id = data["approval"]["id"]

    approve_resp = api_client.post(f"/approvals/{approval_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "APPROVED"

    provider = provider_manager.providers[worker_id]
    assert provider.sent_messages[-1] == "rm -rf /tmp"

    approvals_list = api_client.get("/approvals", params={"status_filter": "APPROVED"}).json()
    assert any(item["id"] == approval_id for item in approvals_list)

    with session_scope() as db:
        stored = db.get(ApprovalORM, approval_id)
        assert stored.metadata_payload == "dangerous"
        assert stored.status == ApprovalStatus.APPROVED

    delete_worker = api_client.delete(f"/terminals/{worker_id}")
    assert delete_worker.status_code == 204
    assert worker_id not in provider_manager.providers

    delete_conductor = api_client.delete(f"/terminals/{supervisor_id}")
    assert delete_conductor.status_code == 204


def test_api_flow_register_and_run(api_client, provider_manager, tmp_path):
    flow_file = tmp_path / "flow.md"
    flow_file.write_text("Run the smoke flow.", encoding="utf-8")

    register_resp = api_client.post(
        "/flows",
        json={
            "name": "smoke",
            "file_path": str(flow_file),
            "schedule": "*/5 * * * *",
            "agent_profile": "conductor",
            "script": "Say flow ran",
        },
    )
    assert register_resp.status_code == 201
    assert register_resp.json()["next_run"] is not None

    run_resp = api_client.post("/flows/smoke/run")
    assert run_resp.status_code == 202
    data = run_resp.json()
    assert data["status"] == "triggered"
    terminal_id = data["terminal_id"]

    provider = provider_manager.providers[terminal_id]
    assert provider.sent_messages[-1] == "Say flow ran"
