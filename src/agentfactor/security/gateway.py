"""Three-state action policy and MCP gateway with one-use execution grants."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client
from jsonschema import ValidationError, validate

from agentfactor import constants
from agentfactor.clients.database import (
    SecurityActionDecision,
    SecurityExecutionGrant,
    SecurityExecutionResult,
    SecurityMCPServer,
    SecurityMCPToolVersion,
    SecurityRiskAlert,
    session_scope,
)
from agentfactor.security.schemas import CanonicalSecurityAction, MCPCallRequest, MCPInventoryRequest


CONFIG_ENV = "AGENTFACTOR_SECURITY_GATEWAY_CONFIG"
EFFECTS = {"record", "remind", "block"}
SAFE_ID = re.compile(r"[A-Za-z0-9_.:@+-]{1,128}")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


class SecurityGatewayError(RuntimeError):
    """A gateway request was invalid or could not be enforced safely."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    except (TypeError, ValueError) as exc:
        raise SecurityGatewayError("action_not_canonical") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class SecurityGatewayService:
    def __init__(self, config_path: Path | None = None) -> None:
        configured = os.getenv(CONFIG_ENV)
        self.config_path = config_path or (Path(configured) if configured else constants.HOME_DIR / "security-gateway.json")

    def evaluate(self, action: CanonicalSecurityAction) -> dict[str, Any]:
        config = self._load_config()
        normalized = self._normalize_action(action)
        effect, reason_code, rule_id = self._match_policy(config, normalized)
        return self._record_decision(normalized, config, effect, reason_code, rule_id)

    def consume(self, token: str, action: CanonicalSecurityAction) -> dict[str, Any]:
        if not token or len(token) > 256:
            raise SecurityGatewayError("grant_invalid", 403)
        config = self._load_config()
        normalized = self._normalize_action(action)
        fingerprint = self._fingerprint(normalized, config["policy_digest"])
        token_hash = _digest(token)
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            grant = db.query(SecurityExecutionGrant).filter(
                SecurityExecutionGrant.token_hash == token_hash,
                SecurityExecutionGrant.action_fingerprint == fingerprint,
                SecurityExecutionGrant.policy_digest == config["policy_digest"],
                SecurityExecutionGrant.consumed_at.is_(None),
                SecurityExecutionGrant.expires_at > now,
            ).first()
            if grant is None:
                raise SecurityGatewayError("grant_invalid_expired_or_consumed", 403)
            updated = db.query(SecurityExecutionGrant).filter(
                SecurityExecutionGrant.id == grant.id,
                SecurityExecutionGrant.consumed_at.is_(None),
            ).update({SecurityExecutionGrant.consumed_at: now})
            if updated != 1:
                raise SecurityGatewayError("grant_already_consumed", 403)
            return {
                "authorized": True,
                "grant_id": grant.id,
                "decision_id": grant.decision_id,
                "action_fingerprint": fingerprint,
            }

    def decisions(self, limit: int = 200) -> list[dict[str, Any]]:
        with session_scope() as db:
            values = db.query(SecurityActionDecision).order_by(SecurityActionDecision.created_at.desc()).limit(limit).all()
            return [self._decision_dict(value) for value in values]

    def alerts(self, limit: int = 200) -> list[dict[str, Any]]:
        with session_scope() as db:
            values = db.query(SecurityRiskAlert).order_by(SecurityRiskAlert.created_at.desc()).limit(limit).all()
            return [{
                "id": value.id,
                "decision_id": value.decision_id,
                "integration_id": value.integration_id,
                "source_event_id": value.source_event_id,
                "action_fingerprint": value.action_fingerprint,
                "effect": value.effect,
                "reason_code": value.reason_code,
                "created_at": value.created_at,
            } for value in values]

    def record_execution_result(
        self,
        decision_id: str,
        grant_id: str,
        succeeded: bool,
        result_summary: Any,
        duration_ms: int,
    ) -> str:
        result_id = f"result_{uuid.uuid4().hex}"
        with session_scope() as db:
            db.add(SecurityExecutionResult(
                id=result_id,
                decision_id=decision_id,
                grant_id=grant_id,
                succeeded=succeeded,
                result_digest=_digest(result_summary),
                duration_ms=max(0, duration_ms),
            ))
        return result_id

    async def discover_mcp(self) -> list[dict[str, Any]]:
        config = self._load_config()
        for server_id, server_config in config["mcp_servers"].items():
            await self._discover_server(server_id, server_config)
        return self.mcp_inventory()

    async def list_mcp_for_agent(self, request: MCPInventoryRequest) -> dict[str, Any]:
        config = self._load_config()
        action = self._inventory_action(request)
        effect, reason_code, rule_id = self._match_policy(config, action)
        decision = self._record_decision(action, config, effect, reason_code, rule_id)
        if effect == "block":
            raise SecurityGatewayError("mcp_inventory_blocked", 403)
        self.consume(decision["execution_grant"], CanonicalSecurityAction.model_validate(action))
        inventory = await self.discover_mcp()
        return {
            "decision": {key: value for key, value in decision.items() if key != "execution_grant"},
            "servers": inventory,
        }

    def mcp_inventory(self) -> list[dict[str, Any]]:
        config = self._load_config()
        with session_scope() as db:
            for server_id, server_config in config["mcp_servers"].items():
                server = db.get(SecurityMCPServer, server_id)
                if server is None:
                    db.add(SecurityMCPServer(
                        id=server_id,
                        display_name=server_config["display_name"],
                        transport=server_config["transport"],
                        config_digest=_digest(server_config["transport_config"]),
                        status="undiscovered",
                    ))
            db.flush()
            servers = db.query(SecurityMCPServer).order_by(SecurityMCPServer.id).all()
            for server in servers:
                if server.id not in config["mcp_servers"]:
                    server.status = "unconfigured"
            tools = db.query(SecurityMCPToolVersion).filter(SecurityMCPToolVersion.current.is_(True)).order_by(
                SecurityMCPToolVersion.server_id, SecurityMCPToolVersion.tool_name
            ).all()
            by_server: dict[str, list[dict[str, Any]]] = {}
            for tool in tools:
                by_server.setdefault(tool.server_id, []).append({
                    "name": tool.tool_name,
                    "schema_digest": tool.schema_digest,
                    "status": tool.status,
                    "first_seen_at": tool.first_seen_at,
                    "last_seen_at": tool.last_seen_at,
                })
            return [{
                "id": server.id,
                "display_name": server.display_name,
                "transport": server.transport,
                "config_digest": server.config_digest,
                "status": server.status,
                "last_error": server.last_error,
                "last_discovered_at": server.last_discovered_at,
                "tools": by_server.get(server.id, []),
            } for server in servers]

    async def call_mcp(self, server_id: str, tool_name: str, request: MCPCallRequest) -> dict[str, Any]:
        config = self._load_config()
        server_config = config["mcp_servers"].get(server_id)
        if server_config is None:
            action = self._mcp_action(server_id, tool_name, request)
            decision = self._record_decision(action, config, "block", "mcp_server_unknown", None)
            raise SecurityGatewayError(decision["reason_code"], 403)
        if tool_name not in server_config["tools"]:
            action = self._mcp_action(server_id, tool_name, request)
            decision = self._record_decision(action, config, "block", "mcp_tool_unknown", None)
            raise SecurityGatewayError(decision["reason_code"], 403)

        discovered = await self._discover_server(server_id, server_config)
        tool = discovered.get(tool_name)
        action = self._mcp_action(server_id, tool_name, request)
        if tool is None:
            self._record_decision(action, config, "block", "mcp_tool_not_advertised", None)
            raise SecurityGatewayError("mcp_tool_not_advertised", 403)
        if tool["status"] != "allowed":
            self._record_decision(action, config, "block", "mcp_schema_changed", None)
            raise SecurityGatewayError("mcp_schema_changed", 409)
        try:
            validate(instance=request.arguments, schema=tool["input_schema"])
        except ValidationError as exc:
            self._record_decision(action, config, "block", "mcp_arguments_invalid", None)
            raise SecurityGatewayError("mcp_arguments_invalid", 400) from exc

        tool_policy = server_config["tools"][tool_name]
        effect = tool_policy["effect"]
        decision = self._record_decision(action, config, effect, f"mcp_tool_{effect}", f"mcp:{server_id}:{tool_name}")
        if effect == "block":
            raise SecurityGatewayError("mcp_tool_blocked", 403)
        consumed = self.consume(decision["execution_grant"], CanonicalSecurityAction.model_validate(action))
        started = time.monotonic()
        succeeded = False
        result_value: dict[str, Any] = {}
        try:
            async with Client({"mcpServers": {server_id: server_config["transport_config"]}}, timeout=120) as client:
                result = await client.call_tool(tool_name, request.arguments, raise_on_error=False)
            result_value = result.model_dump(mode="json", by_alias=True)
            succeeded = not bool(result_value.get("isError", result_value.get("is_error", False)))
            return {
                "decision": {key: value for key, value in decision.items() if key != "execution_grant"},
                "result": result_value,
            }
        except Exception as exc:
            raise SecurityGatewayError("mcp_upstream_call_failed", 502) from exc
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            self.record_execution_result(
                decision["id"],
                consumed["grant_id"],
                succeeded,
                result_value,
                duration_ms,
            )

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raw: dict[str, Any] = {"policy_version": "default-deny", "default_effect": "block", "rules": [], "mcp_servers": {}}
        else:
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SecurityGatewayError("gateway_config_invalid", 503) from exc
        return self.normalize_config(raw)

    def normalize_config(self, raw: Any) -> dict[str, Any]:
        """Validate and canonicalize one publishable gateway configuration."""
        if not isinstance(raw, dict) or set(raw) - {"policy_version", "default_effect", "rules", "mcp_servers"}:
            raise SecurityGatewayError("gateway_config_invalid", 503)
        version = raw.get("policy_version", "")
        default_effect = raw.get("default_effect", "block")
        rules = raw.get("rules", [])
        servers = raw.get("mcp_servers", {})
        if not isinstance(version, str) or not SAFE_ID.fullmatch(version) or default_effect not in EFFECTS or not isinstance(rules, list) or not isinstance(servers, dict):
            raise SecurityGatewayError("gateway_config_invalid", 503)
        normalized_rules = [self._normalize_rule(rule) for rule in rules]
        normalized_servers = {server_id: self._normalize_server(server_id, value) for server_id, value in servers.items()}
        normalized = {
            "policy_version": version,
            "default_effect": default_effect,
            "rules": normalized_rules,
            "mcp_servers": normalized_servers,
        }
        normalized["policy_digest"] = _digest(normalized)
        return normalized

    @staticmethod
    def _normalize_rule(rule: Any) -> dict[str, Any]:
        allowed = {"id", "effect", "principals", "surfaces", "operations", "resource_prefixes"}
        if not isinstance(rule, dict) or set(rule) - allowed or not SAFE_ID.fullmatch(str(rule.get("id", ""))) or rule.get("effect") not in EFFECTS:
            raise SecurityGatewayError("gateway_config_invalid", 503)
        value = {"id": rule["id"], "effect": rule["effect"]}
        for key in ("principals", "surfaces", "operations", "resource_prefixes"):
            items = rule.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, str) or len(item) > 2048 for item in items):
                raise SecurityGatewayError("gateway_config_invalid", 503)
            if key == "resource_prefixes" and "filesystem" in value["surfaces"]:
                items = [os.path.realpath(item) if os.path.isabs(item) else item for item in items]
            value[key] = sorted(set(items))
        return value

    @staticmethod
    def _normalize_server(server_id: str, value: Any) -> dict[str, Any]:
        if not isinstance(server_id, str) or not SAFE_ID.fullmatch(server_id) or not isinstance(value, dict):
            raise SecurityGatewayError("gateway_config_invalid", 503)
        if set(value) - {"display_name", "command", "args", "url", "tools"}:
            raise SecurityGatewayError("gateway_config_invalid", 503)
        display_name = value.get("display_name", server_id)
        tools = value.get("tools", {})
        if not isinstance(display_name, str) or not display_name or len(display_name) > 128 or not isinstance(tools, dict):
            raise SecurityGatewayError("gateway_config_invalid", 503)
        if "url" in value:
            if "command" in value or "args" in value or not isinstance(value["url"], str) or not value["url"].startswith(("http://127.0.0.1", "http://localhost", "https://")):
                raise SecurityGatewayError("gateway_config_invalid", 503)
            transport_config: Any = {"url": value["url"]}
            transport = "http"
        else:
            command = value.get("command")
            args = value.get("args", [])
            if not isinstance(command, str) or not command or not isinstance(args, list) or any(not isinstance(item, str) for item in args):
                raise SecurityGatewayError("gateway_config_invalid", 503)
            transport_config = {"command": command, "args": args}
            transport = "stdio"
        normalized_tools: dict[str, dict[str, Any]] = {}
        for tool_name, tool in tools.items():
            if not isinstance(tool_name, str) or not SAFE_ID.fullmatch(tool_name) or not isinstance(tool, dict) or set(tool) - {"effect", "schema_digest"}:
                raise SecurityGatewayError("gateway_config_invalid", 503)
            effect = tool.get("effect")
            schema_digest = tool.get("schema_digest")
            if effect not in EFFECTS or (schema_digest is not None and not SHA256.fullmatch(schema_digest)):
                raise SecurityGatewayError("gateway_config_invalid", 503)
            normalized_tools[tool_name] = {"effect": effect, "schema_digest": schema_digest}
        return {
            "display_name": display_name,
            "transport": transport,
            "transport_config": transport_config,
            "tools": normalized_tools,
        }

    def _normalize_action(self, action: CanonicalSecurityAction) -> dict[str, Any]:
        value = action.model_dump(mode="json")
        principal = value["principal"]["id"]
        session = value["context"]["session_id"]
        if not SAFE_ID.fullmatch(principal) or not SAFE_ID.fullmatch(session) or not SAFE_ID.fullmatch(value["endpoint_id"]):
            raise SecurityGatewayError("action_identity_invalid")
        cwd = value["context"]["cwd"]
        if not os.path.isabs(cwd):
            raise SecurityGatewayError("action_cwd_not_absolute")
        value["context"]["cwd"] = os.path.realpath(cwd)
        target = value["action"]
        if len(target["operation"]) > 128 or len(target["resource"]) > 4096 or "\x00" in target["operation"] or "\x00" in target["resource"]:
            raise SecurityGatewayError("action_target_invalid")
        if target["surface"] == "filesystem":
            resource = target["resource"]
            if not os.path.isabs(resource):
                resource = os.path.join(value["context"]["cwd"], resource)
            target["resource"] = os.path.realpath(resource)
        _canonical(value)
        return value

    @staticmethod
    def _fingerprint(action: dict[str, Any], policy_digest: str) -> str:
        return _digest({"action": action, "policy_digest": policy_digest})

    @staticmethod
    def _match_policy(config: dict[str, Any], action: dict[str, Any]) -> tuple[str, str, str | None]:
        target = action["action"]
        for rule in config["rules"]:
            if rule["principals"] and action["principal"]["id"] not in rule["principals"]:
                continue
            if rule["surfaces"] and target["surface"] not in rule["surfaces"]:
                continue
            if rule["operations"] and target["operation"] not in rule["operations"]:
                continue
            if rule["resource_prefixes"] and not any(
                SecurityGatewayService._resource_matches(target["surface"], target["resource"], prefix)
                for prefix in rule["resource_prefixes"]
            ):
                continue
            return rule["effect"], f"policy_{rule['effect']}", rule["id"]
        effect = config["default_effect"]
        return effect, f"policy_default_{effect}", None

    @staticmethod
    def _resource_matches(surface: str, resource: str, prefix: str) -> bool:
        if surface == "filesystem" and os.path.isabs(prefix):
            normalized = prefix.rstrip(os.sep) or os.sep
            if normalized == os.sep:
                return resource.startswith(os.sep)
            return resource == normalized or resource.startswith(normalized + os.sep)
        return resource.startswith(prefix)

    def _record_decision(
        self,
        action: dict[str, Any],
        config: dict[str, Any],
        effect: str,
        reason_code: str,
        rule_id: str | None,
    ) -> dict[str, Any]:
        fingerprint = self._fingerprint(action, config["policy_digest"])
        decision_id = f"decision_{uuid.uuid4().hex}"
        resource_digest = _digest(action["action"]["resource"])
        params_digest = _digest(action["action"]["params"])
        token: str | None = None
        grant_id: str | None = None
        expires_at: datetime | None = None
        with session_scope() as db:
            db.add(SecurityActionDecision(
                id=decision_id,
                action_fingerprint=fingerprint,
                policy_digest=config["policy_digest"],
                policy_version=config["policy_version"],
                effect=effect,
                reason_code=reason_code,
                rule_id=rule_id,
                principal_id=action["principal"]["id"],
                session_id=action["context"]["session_id"],
                surface=action["action"]["surface"],
                operation=action["action"]["operation"],
                resource_digest=resource_digest,
                params_digest=params_digest,
                enforced=effect == "block",
            ))
            if effect in {"remind", "block"}:
                db.add(SecurityRiskAlert(
                    id=f"alert_{uuid.uuid4().hex}",
                    decision_id=decision_id,
                    action_fingerprint=fingerprint,
                    effect=effect,
                    reason_code=reason_code,
                ))
            if effect != "block":
                token = secrets.token_urlsafe(32)
                grant_id = f"grant_{uuid.uuid4().hex}"
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
                db.add(SecurityExecutionGrant(
                    id=grant_id,
                    decision_id=decision_id,
                    token_hash=_digest(token),
                    action_fingerprint=fingerprint,
                    policy_digest=config["policy_digest"],
                    expires_at=expires_at,
                ))
        result = {
            "id": decision_id,
            "effect": effect,
            "allowed": effect != "block",
            "enforced": effect == "block",
            "reason_code": reason_code,
            "rule_id": rule_id,
            "action_fingerprint": fingerprint,
            "policy_digest": config["policy_digest"],
            "policy_version": config["policy_version"],
        }
        if token is not None:
            result.update({"execution_grant": token, "grant_id": grant_id, "grant_expires_at": expires_at.isoformat()})
        return result

    async def _discover_server(self, server_id: str, server_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        now = datetime.now(timezone.utc)
        config_digest = _digest(server_config["transport_config"])
        try:
            async with Client({"mcpServers": {server_id: server_config["transport_config"]}}, timeout=30) as client:
                tools = await client.list_tools()
        except Exception as exc:
            self._store_server(server_id, server_config, config_digest, "offline", "mcp_upstream_unavailable", None)
            raise SecurityGatewayError("mcp_upstream_unavailable", 502) from exc
        self._store_server(server_id, server_config, config_digest, "healthy", None, now)
        discovered: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for tool in tools:
            payload = tool.model_dump(mode="json", by_alias=True)
            name = payload.get("name")
            schema = payload.get("inputSchema", payload.get("input_schema"))
            if not isinstance(name, str) or not SAFE_ID.fullmatch(name) or not isinstance(schema, dict):
                continue
            seen.add(name)
            description = payload.get("description", "")
            if not isinstance(description, str):
                description = ""
            schema_digest = _digest({"input_schema": schema, "description": description})
            status = self._store_tool(server_id, name, schema, schema_digest, server_config["tools"].get(name), now)
            discovered[name] = {"input_schema": schema, "schema_digest": schema_digest, "status": status}
        with session_scope() as db:
            stale = db.query(SecurityMCPToolVersion).filter(
                SecurityMCPToolVersion.server_id == server_id,
                SecurityMCPToolVersion.current.is_(True),
                SecurityMCPToolVersion.tool_name.not_in(seen),
            ).all() if seen else db.query(SecurityMCPToolVersion).filter(
                SecurityMCPToolVersion.server_id == server_id,
                SecurityMCPToolVersion.current.is_(True),
            ).all()
            for item in stale:
                item.current = False
                item.status = "missing"
        return discovered

    @staticmethod
    def _store_server(
        server_id: str,
        config: dict[str, Any],
        config_digest: str,
        status: str,
        error: str | None,
        discovered_at: datetime | None,
    ) -> None:
        with session_scope() as db:
            server = db.get(SecurityMCPServer, server_id)
            if server is None:
                server = SecurityMCPServer(id=server_id, display_name=config["display_name"], transport=config["transport"], config_digest=config_digest, status=status)
                db.add(server)
            server.display_name = config["display_name"]
            server.transport = config["transport"]
            server.config_digest = config_digest
            server.status = status
            server.last_error = error
            if discovered_at is not None:
                server.last_discovered_at = discovered_at

    @staticmethod
    def _store_tool(
        server_id: str,
        tool_name: str,
        schema: dict[str, Any],
        schema_digest: str,
        policy: dict[str, Any] | None,
        now: datetime,
    ) -> str:
        with session_scope() as db:
            current = db.query(SecurityMCPToolVersion).filter(
                SecurityMCPToolVersion.server_id == server_id,
                SecurityMCPToolVersion.tool_name == tool_name,
                SecurityMCPToolVersion.current.is_(True),
            ).first()
            accepted = policy is not None and (policy["schema_digest"] is None or policy["schema_digest"] == schema_digest)
            if current is not None and current.schema_digest != schema_digest:
                current.current = False
                status = "allowed" if policy is not None and policy["schema_digest"] == schema_digest else "schema_changed"
            elif current is not None:
                if current.status == "schema_changed" and policy is not None and policy["schema_digest"] is None:
                    status = "schema_changed"
                else:
                    status = "allowed" if accepted else "blocked"
            else:
                status = "allowed" if accepted else "blocked_unknown"
            tool_id = f"{server_id}:{tool_name}:{schema_digest}"
            value = db.get(SecurityMCPToolVersion, tool_id)
            if value is None:
                value = SecurityMCPToolVersion(
                    id=tool_id,
                    server_id=server_id,
                    tool_name=tool_name,
                    schema_digest=schema_digest,
                    input_schema=_canonical(schema).decode(),
                    status=status,
                    current=True,
                )
                db.add(value)
            else:
                value.status = status
                value.current = True
                value.last_seen_at = now
            return status

    def _mcp_action(self, server_id: str, tool_name: str, request: MCPCallRequest) -> dict[str, Any]:
        return self._normalize_action(CanonicalSecurityAction.model_validate({
            "principal": request.principal.model_dump(),
            "endpoint_id": request.endpoint_id,
            "agent": request.agent.model_dump(),
            "integration_mode": request.integration_mode,
            "context": {
                "project_id": request.project_id,
                "session_id": request.session_id,
                "cwd": request.cwd,
                "tool_call_id": request.tool_call_id,
            },
            "action": {
                "surface": "mcp",
                "operation": "call",
                "resource": f"mcp://{server_id}/{tool_name}",
                "params": request.arguments,
            },
        }))

    def _inventory_action(self, request: MCPInventoryRequest) -> dict[str, Any]:
        return self._normalize_action(CanonicalSecurityAction.model_validate({
            "principal": request.principal.model_dump(),
            "endpoint_id": request.endpoint_id,
            "agent": request.agent.model_dump(),
            "integration_mode": request.integration_mode,
            "context": {
                "project_id": request.project_id,
                "session_id": request.session_id,
                "cwd": request.cwd,
                "tool_call_id": request.tool_call_id,
            },
            "action": {
                "surface": "mcp",
                "operation": "list",
                "resource": "mcp://inventory",
                "params": {},
            },
        }))

    @staticmethod
    def _decision_dict(value: SecurityActionDecision) -> dict[str, Any]:
        return {
            "id": value.id,
            "action_fingerprint": value.action_fingerprint,
            "policy_digest": value.policy_digest,
            "policy_version": value.policy_version,
            "effect": value.effect,
            "reason_code": value.reason_code,
            "rule_id": value.rule_id,
            "principal_id": value.principal_id,
            "session_id": value.session_id,
            "surface": value.surface,
            "operation": value.operation,
            "resource_digest": value.resource_digest,
            "params_digest": value.params_digest,
            "enforced": value.enforced,
            "created_at": value.created_at,
        }
