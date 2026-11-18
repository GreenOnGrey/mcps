"""The 401 gate of the HTTP transport (FTR.INFRA.MCPS-0001 R2, R11).

mcp-clickhouse requires exactly one inbound mode, and its static token cannot carry the
credentials of a user. The server therefore starts with CLICKHOUSE_MCP_AUTH_DISABLED=true, and
this gate is the only entrance: a request without `Authorization: Bearer <login>:<password>`
never reaches the tools. `GET /health` stays open for the probe of the stand.
"""

from __future__ import annotations

from .middleware import AUTH_HEADER, parse_authorization

OPEN_PATHS = frozenset({"/health", "/healthz"})
ERROR_BODY = b'{"error":"authentication required: send Authorization: Bearer <login>:<password>"}'


class AuthGate:
    """An ASGI middleware: 401 before the MCP transport."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        if parse_authorization(headers.get(AUTH_HEADER)) is None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(ERROR_BODY)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": ERROR_BODY})
            return
        await self.app(scope, receive, send)
