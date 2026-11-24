"""The entry point of the Greenplum MCP server of the stand (FTR.INFRA.MCPS-0002 tech §2).

Five tools, all read-only: `run_query`, `list_schemas`, `list_tables`, `describe_table`,
`view_definition`. The credentials of the user arrive in every request from the proxy of Nabu
(FTR.NAB.CMN-0001 R8), the gate refuses a request without them, and the session of the database is
read-only with the limits of the chart.
"""

from __future__ import annotations

import json
import os

import uvicorn
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.responses import JSONResponse

from . import db
from .gate import AuthGate
from .middleware import current, setup_middleware

mcp = FastMCP("greenplum-mcp")


@mcp.custom_route("/health", methods=["GET"])
async def health(_request) -> JSONResponse:
    """The probe of the stand and of docker compose: the listener, not the state of the database."""
    return JSONResponse({"status": "ok"})


def _run(fn, *args) -> dict:
    """A tool body: refusals and database errors become the text of the tool (R8)."""
    try:
        return fn(*args, credentials=current())
    except db.QueryError as error:
        raise ToolError(str(error)) from error
    except Exception as error:  # psycopg2.Error and anything the driver raises
        code = getattr(error, "pgcode", None) or ""
        message = getattr(error, "pgerror", None) or str(error)
        position = getattr(error, "diag", None)
        where = getattr(position, "statement_position", None) if position is not None else None
        text = f"{code} {message}".strip() if code else message
        if where:
            text += f" (position {where})"
        raise ToolError(text) from error


@mcp.tool
def run_query(query: str) -> dict:
    """Run one read-only SELECT against Greenplum.

    Args:
        query: one SELECT or WITH … SELECT statement; several statements and any change of data are refused.
    """
    return _run(db.run_query, query)


@mcp.tool
def list_schemas() -> dict:
    """List the schemas the connected user may use."""
    return _run(db.list_schemas)


@mcp.tool
def list_tables(schema: str) -> dict:
    """List tables and views of a schema.

    Args:
        schema: the schema name, for example mart.
    """
    return _run(db.list_tables, schema)


@mcp.tool
def describe_table(schema: str, table: str) -> dict:
    """Describe the columns of a table or a view: name, type, size, nullability and comment.

    Args:
        schema: the schema name, for example mart.
        table: the table or view name.
    """
    return _run(db.describe_table, schema, table)


@mcp.tool
def view_definition(schema: str, view: str) -> dict:
    """Read the SQL of a view, as `pg_get_viewdef` returns it.

    Args:
        schema: the schema name, for example mart.
        view: the view name.
    """
    return _run(db.view_definition, schema, view)


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
