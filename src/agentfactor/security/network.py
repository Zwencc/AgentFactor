"""Pinned-DNS network egress with route, IP, protocol, and redirect enforcement."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from agentfactor.security.dataflow import DataFlowService


MAXIMUM_NETWORK_BYTES = 1 << 20
MAXIMUM_REDIRECTS = 5
BLOCKED_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "host",
    "proxy-authorization",
    "proxy-connection",
}


class NetworkEgressError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: int) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class NetworkEgressService:
    def __init__(self, dataflow: DataFlowService | None = None) -> None:
        self.dataflow = dataflow or DataFlowService()

    def request(
        self,
        *,
        session_id: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
        timeout_seconds: int,
        authorize: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
        record: Callable[[dict[str, Any], dict[str, Any], bool, dict[str, Any], int], None],
        surface: str = "network",
        allow_authorization: bool = False,
        egress_destination: str | None = None,
    ) -> dict[str, Any]:
        self._validate_headers(headers, allow_authorization)
        current = url
        hops: list[dict[str, Any]] = []
        authorization_host: str | None = None
        for redirect_count in range(MAXIMUM_REDIRECTS + 1):
            normalized, host, port, address, route_id = self._resolve(current)
            if "authorization" in {name.lower() for name in headers}:
                if authorization_host is None:
                    authorization_host = host
                elif authorization_host != host:
                    raise NetworkEgressError("credential_redirect_blocked", 403)
            egress = self.dataflow.authorize_egress(
                session_id, surface, egress_destination or normalized
            )
            decision, grant = authorize(normalized, {
                "method": method,
                "body_digest_present": body is not None,
                "resolved_ip": address,
                "route_id": route_id,
            })
            import time
            started = time.monotonic()
            succeeded = False
            summary: dict[str, Any] = {"resolved_ip": address, "route_id": route_id}
            try:
                status, response_headers, content = self._request_once(
                    method, normalized, host, port, address, headers, body, timeout_seconds
                )
                summary.update({"status": status, "bytes": len(content)})
                succeeded = 200 <= status < 400
            finally:
                record(decision, grant, succeeded, summary, int((time.monotonic() - started) * 1000))
            hops.append({"url": normalized, "resolved_ip": address, "status": status, "route_id": route_id})
            location = response_headers.get("location")
            if status not in {301, 302, 303, 307, 308} or not location:
                return {
                    "status": status,
                    "headers": response_headers,
                    "body": content.decode("utf-8", errors="replace"),
                    "hops": hops,
                    "data_flow": egress,
                }
            if redirect_count == MAXIMUM_REDIRECTS:
                raise NetworkEgressError("network_redirect_limit", 502)
            current = urljoin(normalized, location)
            if status == 303:
                method, body = "GET", None
        raise NetworkEgressError("network_redirect_limit", 502)

    def _resolve(self, url: str) -> tuple[str, str, int, str, str]:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise NetworkEgressError("network_url_invalid")
        host = parts.hostname.rstrip(".").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        document, _policy_digest = self.dataflow.active_document()
        route = next((item for item in document.get("network_routes", []) if (
            parts.scheme in item.get("schemes", [])
            and port in item.get("ports", [])
            and any(self._host_matches(host, pattern) for pattern in item.get("host_patterns", []))
        )), None)
        if route is None:
            raise NetworkEgressError("network_route_blocked", 403)
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        except socket.gaierror as exc:
            raise NetworkEgressError("network_dns_failed", 502) from exc
        if not addresses:
            raise NetworkEgressError("network_dns_failed", 502)
        parsed = [ipaddress.ip_address(address) for address in addresses]
        if not route.get("allow_private_ips", False) and any(not address.is_global for address in parsed):
            raise NetworkEgressError("network_private_ip_blocked", 403)
        path = parts.path or "/"
        normalized = urlunsplit((parts.scheme, f"{host}:{port}", path, parts.query, ""))
        return normalized, host, port, str(parsed[0]), route.get("id", "")

    @staticmethod
    def _host_matches(host: str, pattern: str) -> bool:
        value = pattern.rstrip(".").lower()
        if value.startswith("*."):
            suffix = value[1:]
            return host.endswith(suffix) and host != value[2:]
        return host == value

    @staticmethod
    def _validate_headers(headers: dict[str, str], allow_authorization: bool = False) -> None:
        for name, value in headers.items():
            if (
                not name
                or (name.lower() in BLOCKED_REQUEST_HEADERS and not (
                    allow_authorization and name.lower() == "authorization"
                ))
                or "\r" in name
                or "\n" in name
                or "\r" in value
                or "\n" in value
            ):
                raise NetworkEgressError("network_header_blocked")

    @staticmethod
    def _request_once(
        method: str,
        url: str,
        host: str,
        port: int,
        address: str,
        headers: dict[str, str],
        body: str | None,
        timeout_seconds: int,
    ) -> tuple[int, dict[str, str], bytes]:
        parts = urlsplit(url)
        connection: http.client.HTTPConnection
        if parts.scheme == "https":
            connection = _PinnedHTTPSConnection(host, port, address, timeout_seconds)
        else:
            connection = http.client.HTTPConnection(address, port, timeout=timeout_seconds)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        request_headers = {**headers, "Host": host if port in {80, 443} else f"{host}:{port}"}
        try:
            connection.request(method, target, body=body, headers=request_headers)
            response = connection.getresponse()
            content = response.read(MAXIMUM_NETWORK_BYTES + 1)
            if len(content) > MAXIMUM_NETWORK_BYTES:
                raise NetworkEgressError("network_response_too_large", 413)
            response_headers = {name.lower(): value for name, value in response.getheaders()}
            return response.status, response_headers, content
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise NetworkEgressError("network_upstream_failed", 502) from exc
        finally:
            connection.close()
