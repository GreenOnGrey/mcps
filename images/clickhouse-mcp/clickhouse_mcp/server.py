"""The entry point of the ClickHouse MCP server of the stand (FTR.INFRA.MCPS-0001 tech §3.4).

The upstream main() cannot be used: it has no place for our ASGI gate. Everything else is the
upstream: its ClickHouseFastMCP, its tools and its health route.
"""

from __future__ import annotations

import os

import uvicorn

from clickhouse_mcp.gate import AuthGate
from clickhouse_mcp.middleware import setup_middleware
from mcp_clickhouse.mcp_server import mcp


def build_app():
    setup_middleware(mcp)
    app = mcp.http_app(path="/mcp")
    app.add_middleware(AuthGate)
    return app


def main() -> None:
    uvicorn.run(
        build_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "warning"),
        access_log=False,
    )


if __name__ == "__main__":
    main()
