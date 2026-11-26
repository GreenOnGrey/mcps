"""The entry point of the web search MCP server of the stand (FTR.INFRA.MCPS-0003 tech §2).

Two tools, both read-only: `web_search` finds pages through DeepSeek Search, `web_fetch` reads
one public page as Markdown through DeepSeek Fetch. Two keys, two roles (R2, R2a): the platform key
of Secret `mcp-search-key` authenticates the caller, the DeepSeek key of Secret `mcp-deepseek-key`
goes to the vendor and never leaves the cluster. `GET /health` and `GET /metrics` stay open, so the
readiness of the pod does not depend on the vendor (R8).
"""

from __future__ import annotations

import hmac
import os
import time

import uvicorn
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import JSONResponse, Response

from . import config, deepseek

mcp = FastMCP("search-mcp")

# Both tools only read: the catalog of Nabu shows «изменяет данные» for a tool without readOnlyHint, and the
# read-only preset of a position would miss them (FTR.INFRA.MCPS-0003 R4). The web is open and changes, so
# the answers are neither deterministic nor repeatable.
READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

REQUESTS = Counter("search_requests_total", "Tool calls by operation and result", ["op", "result"])
DURATION = Histogram("search_request_seconds", "Duration of a tool call, seconds", ["op"])
TRUNCATED = Counter("search_content_truncated_total", "Answers whose content was cut by the limit")


@mcp.custom_route("/health", methods=["GET"])
async def health(_request) -> JSONResponse:
    """The probe of the stand and of docker compose: the listener, not the state of the vendor."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(_request) -> Response:
    """Prometheus metrics of the server (arch §7)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _run(op: str, call, *args) -> dict:
    """A tool body: a refusal of the vendor becomes the text of the tool (R6)."""
    started = time.monotonic()
    try:
        answer = call(config.load(), *args)
    except deepseek.Refusal as error:
        REQUESTS.labels(op, error.result).inc()
        raise ToolError(str(error)) from None
    except Exception:  # a bug, or a network error that httpx did not wrap
        REQUESTS.labels(op, "error").inc()
        raise ToolError("the search is unavailable: retry later") from None
    finally:
        DURATION.labels(op).observe(time.monotonic() - started)
    REQUESTS.labels(op, "empty" if op == "search" and not answer.get("results") else "ok").inc()
    if answer.get("truncated"):
        TRUNCATED.inc()
    return answer


@mcp.tool(annotations=READ_ONLY)
def web_search(query: str, count: int = 10, need_content: bool = False) -> dict:
    """Search the web and return ranked results with a snippet and a long summary for the model.

    Args:
        query: 1 to 128 characters; operators site:, -site:, "…", lang:, after: and before: work inside it.
        count: how many results, 1 to 50, 10 by default.
        need_content: also return the page body when the vendor has it; off by default.
    """
    return _run("search", deepseek.search, query, count, need_content)


@mcp.tool(annotations=READ_ONLY)
def web_fetch(url: str) -> dict:
    """Read one public page as Markdown; an address of the company network cannot be read.

    Args:
        url: an http(s) address of a public page.
    """
    return _run("fetch", deepseek.fetch, url)


class AuthGate:
    """An ASGI middleware: 401 before the transport when the platform key is missing or wrong."""

    OPEN = frozenset({"/health", "/metrics"})
    ERROR_BODY = b'{"error":"unauthorized"}'

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in self.OPEN:
            await self.app(scope, receive, send)
            return
        key = config.load().platform_key.encode()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if not key or scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), key):
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(self.ERROR_BODY)).encode("ascii"))],
            })
            await send({"type": "http.response.body", "body": self.ERROR_BODY})
            return
        await self.app(scope, receive, send)


def build_app():
    app = mcp.http_app(path="/mcp")
    app.add_middleware(AuthGate)
    return app


def main() -> None:
    uvicorn.run(
        build_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
