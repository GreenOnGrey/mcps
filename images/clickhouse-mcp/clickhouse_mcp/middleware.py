"""The personal token of the user as the credentials of a ClickHouse query.

The proxy of Nabu puts `Authorization: Bearer <login>:<password>` into every request
(FTR.NAB.CMN-0001 R8, FTR.INFRA.MCPS-0001 R2). The gate answers 401 when the header is
missing or is not a pair; the middleware of FastMCP passes the pair to mcp-clickhouse as a
request-scoped override, together with readonly=1 and the limits of the chart (R9).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

# The key of mcp-clickhouse: a request-scoped override of the ClickHouse client (tech §2.3).
OVERRIDES_KEY = "clickhouse_client_config_overrides"
AUTH_HEADER = "authorization"


@dataclass(frozen=True)
class Credentials:
    login: str
    password: str


def parse_authorization(value: str | None) -> Credentials | None:
    """Read `Bearer login:password`; anything else is no credentials."""
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    login, sep, password = token.partition(":")
    if not sep or not login or not password:
        return None
    return Credentials(login=login, password=password)


def limits() -> dict[str, int]:
    """The limits of the chart: a query cannot run longer or return more rows."""
    return {
        "readonly": 1,
        "max_execution_time": int(os.environ.get("CLICKHOUSE_LIMIT_SECONDS", "30")),
        "max_result_rows": int(os.environ.get("CLICKHOUSE_LIMIT_ROWS", "10000")),
    }


class CredentialsMiddleware(Middleware):
    """Puts the credentials of the request into the ClickHouse client of that request only."""

    async def on_call_tool(self, context, call_next):
        # authorization is among the headers that get_http_headers strips by default.
        headers = get_http_headers(include={AUTH_HEADER})
        credentials = parse_authorization(headers.get(AUTH_HEADER))
        if credentials is None:
            raise ToolError("authentication required: connect the item again in Connections")
        ctx = context.fastmcp_context
        if ctx is not None:
            # serializable=False is required: mcp-clickhouse rejects an override kept in the session,
            # because it would leak the credentials of one user into the calls of another.
            await ctx.set_state(
                OVERRIDES_KEY,
                {
                    "username": credentials.login,
                    "password": credentials.password,
                    "settings": limits(),
                },
                serializable=False,
            )
        return await call_next(context)


def setup_middleware(mcp) -> None:
    mcp.add_middleware(CredentialsMiddleware())
