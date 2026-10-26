"""OIDC authentication and local role-based authorization."""

from __future__ import annotations

import hmac
import os
import re
import uuid
from typing import Any

import jwt

from agentfactor.clients.database import SecurityRoleBinding, session_scope


PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}")
ROLE_PERMISSIONS = {
    "viewer": {"security:read"},
    "policy_author": {"security:read", "policy:author"},
    "policy_publisher": {"security:read", "policy:publish"},
    "responder": {"security:read", "response:execute"},
    "admin": {"*"},
}


class SecurityAccessError(RuntimeError):
    def __init__(self, code: str, status_code: int = 403) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class SecurityAccessService:
    def authenticate(self, authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise SecurityAccessError("authentication_required", 401)
        token = authorization[7:]
        bootstrap = os.getenv("AGENTFACTOR_SECURITY_ADMIN_TOKEN")
        if bootstrap and hmac.compare_digest(token, bootstrap):
            principal = os.getenv("AGENTFACTOR_SECURITY_ADMIN_PRINCIPAL", "local-admin")
            if not PRINCIPAL.fullmatch(principal):
                raise SecurityAccessError("bootstrap_principal_invalid", 503)
            return f"bootstrap:{principal}"
        issuer = os.getenv("AGENTFACTOR_OIDC_ISSUER")
        audience = os.getenv("AGENTFACTOR_OIDC_AUDIENCE")
        jwks_url = os.getenv("AGENTFACTOR_OIDC_JWKS_URL")
        if not issuer or not audience or not jwks_url:
            raise SecurityAccessError("oidc_not_configured", 503)
        try:
            signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=audience,
                issuer=issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise SecurityAccessError("oidc_token_invalid", 401) from exc
        principal = claims.get("sub", "")
        if not isinstance(principal, str) or not PRINCIPAL.fullmatch(principal):
            raise SecurityAccessError("oidc_subject_invalid", 401)
        return principal

    def authorize(self, principal_id: str, permission: str) -> None:
        if principal_id.startswith("bootstrap:"):
            return
        with session_scope() as db:
            roles = [value.role for value in db.query(SecurityRoleBinding).filter(
                SecurityRoleBinding.principal_id == principal_id
            ).all()]
        if not any("*" in ROLE_PERMISSIONS.get(role, set()) or permission in ROLE_PERMISSIONS.get(role, set()) for role in roles):
            raise SecurityAccessError("permission_denied")

    def bind(self, actor_id: str, principal_id: str, role: str) -> dict[str, str]:
        self.authorize(actor_id, "*")
        if not PRINCIPAL.fullmatch(principal_id) or role not in ROLE_PERMISSIONS:
            raise SecurityAccessError("role_binding_invalid", 400)
        with session_scope() as db:
            existing = db.query(SecurityRoleBinding).filter(
                SecurityRoleBinding.principal_id == principal_id,
                SecurityRoleBinding.role == role,
            ).first()
            if existing is not None:
                return {"id": existing.id, "principal_id": principal_id, "role": role}
            value = SecurityRoleBinding(
                id=f"role_binding_{uuid.uuid4().hex}",
                principal_id=principal_id,
                role=role,
                created_by=actor_id,
            )
            db.add(value)
            return {"id": value.id, "principal_id": principal_id, "role": role}
