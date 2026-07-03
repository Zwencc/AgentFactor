import json

from agentfactor.clients.database import (
    LLMProviderConfig,
    TerminalAnalysis,
    VerifierCheck,
    VerifierRun,
    WorkItem,
    session_scope,
)
from agentfactor.models.enums import WorkItemStatus, WorkItemType
from agentfactor.models.review import LLMProviderCreateRequest
from agentfactor.services.llm_review_service import LLMReviewService


def _work_item() -> WorkItem:
    return WorkItem(
        id="work_review",
        project_id="qa-review",
        title="Implement API",
        description="Add the review endpoint.",
        type=WorkItemType.FEATURE,
        status=WorkItemStatus.NEEDS_VERIFICATION,
        acceptance_criteria=json.dumps(["Endpoint returns structured JSON"]),
        files_of_interest=json.dumps(["src/api.py"]),
    )


def _analysis() -> TerminalAnalysis:
    return TerminalAnalysis(
        terminal_id="term_review",
        provider="codex",
        tool_stats=json.dumps({"Edit": 1, "Bash": 1}),
        files_touched=json.dumps(["src/api.py"]),
        commands_run=json.dumps(["pytest tests/test_api.py"]),
        risk_flags=json.dumps([]),
        work_item_id="work_review",
        compliance_summary=json.dumps({"ok": True}),
        line_count=20,
        raw_log="edited src/api.py and ran pytest",
        conversation_turns=json.dumps([
            {"type": "human", "content": "Add endpoint", "index": 0},
            {"type": "agent", "content": "Implemented and tested", "index": 1},
        ]),
        review_status="pending",
    )


def test_review_without_active_provider_records_skipped_run():
    with session_scope() as db:
        db.add(_work_item())
        db.add(_analysis())

    result = LLMReviewService().review_terminal("term_review")

    assert result["status"] == "skipped"
    assert result["review_error"] == "no_active_llm_provider"
    assert result["verifier_run"]["status"] == "skipped"
    assert result["verifier_run"]["checks"][0]["status"] == "skipped"

    with session_scope() as db:
        assert db.query(VerifierRun).count() == 1
        assert db.query(VerifierCheck).count() == 1


def test_review_with_active_provider_persists_report(monkeypatch):
    with session_scope() as db:
        db.add(_work_item())
        db.add(_analysis())
        db.add(LLMProviderConfig(
            name="Local",
            provider_type="openai_compatible",
            base_url="http://example.test/v1",
            api_key="secret",
            model="review-model",
            is_active=True,
        ))

    def fake_call(self, prompt, config):
        assert "Endpoint returns structured JSON" in prompt
        assert config["model"] == "review-model"
        return json.dumps({
            "compliance_score": 91,
            "verdict": "pass",
            "requirement_checks": [
                {
                    "criterion": "Endpoint returns structured JSON",
                    "status": "met",
                    "evidence": "pytest was run",
                    "suggestion": "",
                }
            ],
            "deviation_summary": "The implementation matches the requirement.",
            "work_item_improvements": {"description": "", "add_criteria": [], "clarify": []},
            "risk_assessment": "low",
            "reviewer_notes": "",
        })

    monkeypatch.setattr(LLMReviewService, "_call_provider", fake_call)

    result = LLMReviewService().review_terminal("term_review")

    assert result["status"] == "done"
    assert result["review"]["compliance_score"] == 91
    assert result["verifier_run"]["status"] == "pass"
    assert result["verifier_run"]["checks"][0]["score"] == 91

    with session_scope() as db:
        analysis = db.query(TerminalAnalysis).filter_by(terminal_id="term_review").one()
        assert analysis.review_status == "done"
        assert analysis.review_model == "review-model"
        assert json.loads(analysis.llm_review)["verdict"] == "pass"


def test_provider_create_auto_activates_first_and_redacts_key():
    service = LLMReviewService()

    first = service.create_provider(LLMProviderCreateRequest(
        name="First",
        provider_type="openai_compatible",
        base_url="http://first.test/v1",
        api_key="first-key",
        model="first-model",
    ))
    second = service.create_provider(LLMProviderCreateRequest(
        name="Second",
        provider_type="openai_compatible",
        base_url="http://second.test/v1",
        api_key="second-key",
        model="second-model",
        is_active=True,
    ))

    providers = service.list_providers()

    assert first["is_active"] is True
    assert second["is_active"] is True
    assert [p["is_active"] for p in providers] == [False, True]
    assert all("api_key" not in p for p in providers)
    assert all(p["api_key_set"] for p in providers)


def test_provider_api_crud_redacts_key(api_client):
    created = api_client.post("/llm-providers", json={
        "name": "API Provider",
        "provider_type": "openai_compatible",
        "base_url": "http://api-provider.test/v1",
        "api_key": "hidden",
        "model": "api-model",
    })

    assert created.status_code == 201
    payload = created.json()
    assert payload["is_active"] is True
    assert payload["api_key_set"] is True
    assert "api_key" not in payload

    listed = api_client.get("/llm-providers")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "API Provider"

    updated = api_client.put(f"/llm-providers/{payload['id']}", json={"model": "api-model-v2"})
    assert updated.status_code == 200
    assert updated.json()["model"] == "api-model-v2"

