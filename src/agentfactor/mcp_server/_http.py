"""Shared async httpx helpers for MCP tools."""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from agentfactor import constants

API_BASE = "http://127.0.0.1:9889"


class MCPError(RuntimeError):
    """API call failed or conductor environment not available."""


def terminal_id() -> str:
    tid = os.getenv(constants.TERMINAL_ENV_VAR)
    if not tid:
        raise MCPError("CONDUCTOR_TERMINAL_ID not set — not running inside a conductor terminal.")
    return tid


async def request(
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    timeout: float = 120.0,
) -> Any:
    url = f"{API_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=payload)
    except httpx.ConnectError as exc:
        raise MCPError(f"Cannot reach conductor API at {API_BASE}: {exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise MCPError(f"API {resp.status_code}: {detail}")
    return resp.json() if resp.content else None
