"""The personal token of the user as the credentials of a Greenplum session (FTR.INFRA.MCPS-0002 R2, R8, R9).

The proxy of Nabu puts `Authorization: Bearer <login>:<password>` into every request. The gate
answers 401 when the header is missing or is not a pair; this middleware passes the pair to the
tool call of that request only, through a context variable, so no state of one user can reach
another. Nothing is stored: the value lives while the call runs.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

AUTH_HEADER = "authorization"

# The credentials of the call being served; empty outside a call.
_current: contextvars.ContextVar["Credentials | None"] = contextvars.ContextVar("greenplum_credentials", default=None)


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


def current() -> Credentials:
    """The credentials of this call; a tool without them is a bug, not a user error."""
    credentials = _current.get()
    if credentials is None:
        raise ToolError("authentication required: connect the item again in Connections")
    return credentials


class CredentialsMiddleware(Middleware):
    """Puts the credentials of the request into the context of that call only."""

    async def on_call_tool(self, context, call_next):
        # get_http_headers drops the transport headers and keeps the authorization of the request.
        headers = get_http_headers()
        credentials = parse_authorization(headers.get(AUTH_HEADER))
        if credentials is None:
            raise ToolError("authentication required: connect the item again in Connections")
        token = _current.set(credentials)
        try:
            return await call_next(context)
        finally:
            _current.reset(token)


def setup_middleware(mcp) -> None:
    mcp.add_middleware(CredentialsMiddleware())
