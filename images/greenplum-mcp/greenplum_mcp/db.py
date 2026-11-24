"""Read-only access to Greenplum: one connection per call, one SELECT per call (FTR.INFRA.MCPS-0002 R3, R5, R6).

There is no pool: the credentials of a user live only while their call runs, and a connection
opened for one person is never handed to another. The session is read-only and carries
`statement_timeout`, so neither a mistake of the model nor a long query reaches the cluster
unbounded.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal

# Words that never appear in a read-only statement. A quoted identifier or a string with such a
# word is not a statement: the scanner strips both before the check.
FORBIDDEN = ("insert", "update", "delete", "merge", "create", "alter", "drop", "truncate", "grant",
             "revoke", "copy", "vacuum", "analyze", "refresh", "call", "do", "begin", "commit",
             "rollback", "set", "reset", "lock", "into")
FIRST_OK = ("select", "with")
WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")


class QueryError(Exception):
    """A statement the server refuses to send: never a database error."""


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    database: str
    sslmode: str
    ca_cert: str
    limit_seconds: int
    limit_rows: int
    connect_timeout: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            host=os.environ.get("GP_HOST", ""),
            port=int(os.environ.get("GP_PORT", "5432")),
            database=os.environ.get("GP_DB", "citydrive_db"),
            sslmode=os.environ.get("GP_SSLMODE", "prefer"),
            ca_cert=os.environ.get("GP_CA_CERT", ""),
            limit_seconds=int(os.environ.get("GP_LIMIT_SECONDS", "30")),
            limit_rows=int(os.environ.get("GP_LIMIT_ROWS", "10000")),
            connect_timeout=int(os.environ.get("GP_CONNECT_TIMEOUT", "10")),
        )


def scan(sql: str) -> tuple[str, int]:
    """(the statement without comments and string literals, the number of statements).

    Comments `--` and `/* */` and literals `'…'`, `"…"` and `$tag$…$tag$` are removed: a forbidden
    word inside them is data, not a statement. A `;` outside them ends a statement; a trailing one
    is not counted.
    """
    out: list[str] = []
    i, n, statements, seen = 0, len(sql), 0, False
    while i < n:
        two = sql[i:i + 2]
        ch = sql[i]
        if two == "--":
            i = sql.find("\n", i)
            if i < 0:
                break
            continue
        if two == "/*":
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in "'\"":
            quote = ch * 3 if sql[i:i + 3] == ch * 3 else ch
            i += len(quote)
            while i < n:
                if sql[i:i + len(quote)] == quote:
                    i += len(quote)
                    break
                if sql[i] == "\\" and ch == "'":
                    i += 1
                i += 1
            out.append(" ")
            continue
        if ch == "$":
            tag = re.match(r"\$[A-Za-z_0-9]*\$", sql[i:])
            if tag:
                end = sql.find(tag.group(0), i + len(tag.group(0)))
                i = n if end < 0 else end + len(tag.group(0))
                out.append(" ")
                continue
        if ch == ";":
            if seen:
                statements += 1
                seen = False
            out.append(" ")
            i += 1
            continue
        if not ch.isspace():
            seen = True
        out.append(ch)
        i += 1
    if seen:
        statements += 1
    return "".join(out), statements


def check(sql: str) -> str:
    """The statement to send, or QueryError: one SELECT, nothing else (R3)."""
    if not sql or not sql.strip():
        raise QueryError("the query is empty: send one SELECT")
    cleaned, statements = scan(sql)
    if statements > 1:
        raise QueryError("only one SELECT per call: the server does not run several statements")
    words = [word.lower() for word in WORD.findall(cleaned)]
    if not words or words[0] not in FIRST_OK:
        raise QueryError("only SELECT is allowed: this server is read-only")
    bad = sorted({word for word in words if word in FORBIDDEN})
    if bad:
        raise QueryError(f"only SELECT is allowed: the query names {', '.join(bad)}")
    return sql.strip().rstrip(";").strip()


def connect(credentials, cfg: Config | None = None):
    """A read-only session with the limits of the chart, opened for one call."""
    import psycopg2

    cfg = cfg or Config.from_env()
    if not cfg.host:
        raise QueryError("the Greenplum host is not configured on the stand: ask the operator for GP_HOST")
    options = f"-c statement_timeout={cfg.limit_seconds * 1000} -c lock_timeout={cfg.limit_seconds * 1000}"
    kwargs = dict(host=cfg.host, port=cfg.port, dbname=cfg.database, user=credentials.login,
                  password=credentials.password, connect_timeout=cfg.connect_timeout,
                  sslmode=cfg.sslmode, options=options, application_name="greenplum-mcp")
    if cfg.ca_cert:
        kwargs["sslrootcert"] = cfg.ca_cert
    conn = psycopg2.connect(**kwargs)
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _rows(cur, limit: int) -> tuple[list[str], list[list], bool]:
    columns = [column.name for column in (cur.description or [])]
    rows = cur.fetchmany(limit + 1)
    truncated = len(rows) > limit
    return columns, [_json_row(row) for row in rows[:limit]], truncated


def _json_row(row) -> list:
    out = []
    for value in row:
        if value is None or isinstance(value, (bool, int, float, str)):
            out.append(value)
        elif isinstance(value, Decimal):
            out.append(float(value))
        else:
            out.append(str(value))
    return out


def run_query(sql: str, credentials, cfg: Config | None = None) -> dict:
    statement = check(sql)
    cfg = cfg or Config.from_env()
    conn = connect(credentials, cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
            columns, rows, truncated = _rows(cur, cfg.limit_rows)
    finally:
        conn.rollback()
        conn.close()
    return {"columns": columns, "rows": rows, "rowCount": len(rows), "truncated": truncated}


def _select(sql: str, params: tuple, credentials, cfg: Config) -> dict:
    conn = connect(credentials, cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns, rows, truncated = _rows(cur, cfg.limit_rows)
    finally:
        conn.rollback()
        conn.close()
    return {"columns": columns, "rows": rows, "rowCount": len(rows), "truncated": truncated}


def list_schemas(credentials, cfg: Config | None = None) -> dict:
    sql = """
        select n.nspname as schema
        from pg_catalog.pg_namespace n
        where 1=1
            and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
            and n.nspname not like 'pg_temp%'
            and has_schema_privilege(current_user, n.oid, 'USAGE')
        order by 1
    """
    return _select(sql, (), credentials, cfg or Config.from_env())


def list_tables(schema: str, credentials, cfg: Config | None = None) -> dict:
    sql = """
        select t.table_name, t.table_type
        from information_schema.tables t
        where 1=1
            and t.table_schema = %s
        order by 1
    """
    return _select(sql, (schema,), credentials, cfg or Config.from_env())


def describe_table(schema: str, table: str, credentials, cfg: Config | None = None) -> dict:
    sql = """
        select c.column_name
             , c.data_type
             , coalesce(c.character_maximum_length, c.numeric_precision) as size
             , c.is_nullable
             , col_description(to_regclass(format('%I.%I', c.table_schema, c.table_name)), c.ordinal_position) as comment
        from information_schema.columns c
        where 1=1
            and c.table_schema = %s
            and c.table_name = %s
        order by c.ordinal_position
    """
    return _select(sql, (schema, table), credentials, cfg or Config.from_env())


def view_definition(schema: str, view: str, credentials, cfg: Config | None = None) -> dict:
    sql = """
        select pg_get_viewdef(to_regclass(format('%I.%I', %s, %s)), true) as definition
    """
    return _select(sql, (schema, view), credentials, cfg or Config.from_env())
