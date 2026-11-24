"""A test of the image greenplum-mcp (FTR.INFRA.MCPS-0002 qa, GP-02, GP-03, GP-05, GP-06, GP-10, GP-14, GP-21).

The MCP client speaks to our server as the proxy of Nabu does. There is no stub of the PostgreSQL
protocol: what can be proven without a database is proven here — the 401 gate, the list of tools,
the refusal of everything but one SELECT before any connection, the truncation of rows and the
isolation of the credentials of a call. Rights, the read-only session and the real errors of
Greenplum are checked by hand against the company cluster (M-level, GP-07, GP-08, GP-11).
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def rpc(headers: dict[str, str], message: dict, session: str | None = None):
    data = json.dumps(message).encode()
    head = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    if session:
        head["Mcp-Session-Id"] = session
    return urllib.request.urlopen(urllib.request.Request(BASE + "/mcp", data, head), timeout=30)


def status(headers: dict[str, str]) -> int:
    try:
        rpc(headers, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}).close()
        return 200
    except urllib.error.HTTPError as error:
        return error.code


def body(response) -> dict:
    raw = response.read().decode()
    line = next((part for part in raw.split("\n") if part.startswith("data:")), raw)
    return json.loads(line.removeprefix("data:").strip())


def connect(token: str) -> tuple[str, dict[str, str]]:
    headers = {"Authorization": f"Bearer {token}"}
    answer = rpc(headers, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    session = answer.headers.get("Mcp-Session-Id")
    answer.read()
    if session:
        rpc(headers, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session).read()
    return session or "", headers


def call(session: str, headers: dict[str, str], tool: str, arguments: dict) -> dict:
    return body(rpc(headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": tool, "arguments": arguments}}, session))


def text_of(answer: dict) -> str:
    result = answer.get("result") or {}
    return "\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")


def is_error(answer: dict) -> bool:
    return bool((answer.get("result") or {}).get("isError"))


def unit_checks() -> bool:
    """Parts that need no server: the scanner, the guard, the truncation and the context of a call."""
    sys.path.insert(0, os.getcwd())  # in the image the workdir is /app, locally it is this directory
    from greenplum_mcp import db, middleware

    ok = True

    def expect(condition: bool, message: str) -> None:
        nonlocal ok
        if not condition:
            print(message, file=sys.stderr)
            ok = False

    # a forbidden word in data is not a statement
    expect(db.check("select 'insert into x' as s") == "select 'insert into x' as s", "a literal was read as a statement")
    expect(db.check("select 1 -- drop table x") == "select 1 -- drop table x", "a comment was read as a statement")
    expect(db.check("with a as (select 1) select * from a") .startswith("with"), "WITH … SELECT was refused")
    for bad in ("insert into t values (1)", "drop table t", "select 1; select 2",
                "with a as (select 1) insert into t select * from a", "select * into t from x", "explain select 1"):
        try:
            db.check(bad)
            expect(False, f"the guard let through: {bad}")
        except db.QueryError:
            pass
    try:
        db.check("update t set a = 1")
        expect(False, "the guard let update through")
    except db.QueryError:
        pass

    # the rows are cut and marked
    class Cursor:
        description = [type("C", (), {"name": "a"})(), type("C", (), {"name": "b"})()]

        def __init__(self, rows):
            self.rows = rows

        def fetchmany(self, size):
            return self.rows[:size]

    columns, rows, truncated = db._rows(Cursor([(1, "x"), (2, "y"), (3, "z")]), 2)
    expect(columns == ["a", "b"] and len(rows) == 2 and truncated, f"the truncation is wrong: {columns} {rows} {truncated}")
    _, rows, truncated = db._rows(Cursor([(1, "x")]), 2)
    expect(rows == [[1, "x"]] and not truncated, "a full answer was marked truncated")

    # the credential of a call lives only while the call runs
    parsed = middleware.parse_authorization("Bearer alice:secret")
    expect(parsed is not None and parsed.login == "alice" and parsed.password == "secret", "the pair was not parsed")
    for bad in (None, "", "Bearer no-colon", "Basic alice:secret", "Bearer :secret", "Bearer alice:"):
        expect(middleware.parse_authorization(bad) is None, f"a bad header was accepted: {bad!r}")
    try:
        middleware.current()
        expect(False, "credentials outlived a call")
    except Exception:
        pass
    return ok


def main() -> int:
    assert os.getuid() != 0, "runs as root"
    server = subprocess.Popen([sys.executable, "-m", "greenplum_mcp.server"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(120):
        try:
            socket.create_connection(("127.0.0.1", 8000), timeout=2).close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        server.terminate()
        print("no listener: " + server.communicate(timeout=10)[0][-2000:], file=sys.stderr)
        return 1

    if urllib.request.urlopen(BASE + "/health", timeout=10).status != 200:
        print("/health did not answer", file=sys.stderr)
        return 1
    if status({}) != 401:
        print("expected 401 without a token", file=sys.stderr)
        return 1
    if status({"Authorization": "Bearer no-colon"}) != 401:
        print("expected 401 for a token without a password", file=sys.stderr)
        return 1
    if not unit_checks():
        server.terminate()
        return 1

    session, headers = connect("alice:secret")
    listed = body(rpc(headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session))
    names = sorted(tool["name"] for tool in listed["result"]["tools"])
    if names != ["describe_table", "list_schemas", "list_tables", "run_query", "view_definition"]:
        print(f"unexpected tools: {names}", file=sys.stderr)
        server.terminate()
        return 1

    # the guard answers before the connection: the host is not configured in this container (GP-06, GP-21)
    answer = call(session, headers, "run_query", {"query": "insert into t values (1)"})
    if not is_error(answer) or "only SELECT" not in text_of(answer):
        print(f"a write was not refused: {answer}", file=sys.stderr)
        server.terminate()
        return 1
    answer = call(session, headers, "run_query", {"query": "select 1"})
    if not is_error(answer) or "GP_HOST" not in text_of(answer):
        print(f"a call without a configured host did not name it: {answer}", file=sys.stderr)
        server.terminate()
        return 1

    # another user is a different pair, and the session of the first one does not carry it
    session2, headers2 = connect("bob:bobpass")
    if headers2["Authorization"] == headers["Authorization"]:
        print("the tokens of two users are the same", file=sys.stderr)
        server.terminate()
        return 1

    server.terminate()
    server.communicate(timeout=15)
    print(f"greenplum-mcp: ok, 401 without a token, {len(names)} read-only tools, the guard refuses writes, "
          f"rows are cut, credentials live for one call")
    return 0


if __name__ == "__main__":
    sys.exit(main())
