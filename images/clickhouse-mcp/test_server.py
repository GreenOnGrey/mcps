"""A test of the image clickhouse-mcp (FTR.INFRA.MCPS-0001 R2, R3, R9, R11, R14).

A stub of ClickHouse answers the version query of the driver and records every request: the
credentials of the Basic header and the settings of the query. The MCP client speaks to our
server as the proxy of Nabu does. The test proves that the token of a request becomes the
credentials of the query, that readonly=1 and the limits go with it, that two users stay apart
and that no write tool is exposed.
"""

import base64
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

seen: list[dict] = []


def varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def text(value: str) -> bytes:
    raw = value.encode()
    return varint(len(raw)) + raw


def version_block() -> bytes:
    """RowBinaryWithNamesAndTypes: two String columns, one row."""
    return b"".join([varint(2), text("version"), text("timezone"), text("String"), text("String"),
                     text("24.1.1.1"), text("UTC")])


class Stub(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.any()

    def do_GET(self):
        self.any()

    def any(self):
        length = int(self.headers.get("Content-Length") or 0)
        query = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        auth = self.headers.get("Authorization") or ""
        login = password = ""
        if auth.startswith("Basic "):
            try:
                login, _, password = base64.b64decode(auth[6:]).decode().partition(":")
            except Exception:
                pass
        seen.append({"path": self.path, "query": query, "login": login, "password": password})
        if "version()" in query:
            body = version_block()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
        else:
            body = b"stub refuses"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def rpc(headers: dict[str, str], message: dict, session: str | None = None):
    data = json.dumps(message).encode()
    head = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    if session:
        head["Mcp-Session-Id"] = session
    return urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/mcp", data, head), timeout=30)


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


def unit_settings() -> bool:
    """The middleware hands readonly=1 and the limits of the chart to mcp-clickhouse as a request override."""
    import asyncio

    sys.path.insert(0, "/app")
    import clickhouse_mcp.middleware as middleware

    state = {}

    class Context:
        async def set_state(self, key, value, *, serializable=True):
            state["key"] = key
            state["value"] = value
            state["serializable"] = serializable

    class MiddlewareContext:
        fastmcp_context = Context()

    class CallNext:
        async def __call__(self, context):
            return "called"

    middleware.get_http_headers = lambda **kwargs: {"authorization": "Bearer alice:secret"}
    result = asyncio.run(middleware.CredentialsMiddleware().on_call_tool(MiddlewareContext(), CallNext()))
    settings = (state.get("value") or {}).get("settings") or {}
    if result != "called" or state.get("serializable") is not False:
        print(f"the middleware did not pass the credentials: {state}", file=sys.stderr)
        return False
    if state.get("key") != middleware.OVERRIDES_KEY or state["value"].get("username") != "alice":
        print(f"wrong override: {state}", file=sys.stderr)
        return False
    if settings.get("readonly") != 1 or settings.get("max_execution_time") != 30 or settings.get("max_result_rows") != 10000:
        print(f"readonly or the limits are wrong: {settings}", file=sys.stderr)
        return False
    return True


def main() -> int:
    assert os.getuid() != 0, "runs as root"
    stub = http.server.ThreadingHTTPServer(("127.0.0.1", 9100), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()

    server = subprocess.Popen([sys.executable, "-m", "clickhouse_mcp.server"],
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

    if status({}) != 401:
        print("expected 401 without a token", file=sys.stderr)
        return 1
    if status({"Authorization": "Bearer no-colon"}) != 401:
        print("expected 401 for a token without a password", file=sys.stderr)
        return 1

    session, headers = connect("alice:secret")
    listed = body(rpc(headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session))
    names = [tool["name"] for tool in listed["result"]["tools"]]
    if sorted(names) != ["list_databases", "list_tables", "run_query"]:
        print(f"unexpected tools: {names}", file=sys.stderr)
        return 1
    call(session, headers, "list_databases", {})

    session, headers = connect("bob:bobpass")
    call(session, headers, "list_databases", {})

    server.terminate()
    server.communicate(timeout=15)
    stub.shutdown()

    alice = [entry for entry in seen if entry["login"] == "alice"]
    bob = [entry for entry in seen if entry["login"] == "bob"]
    if not alice or not bob:
        print(f"the credentials did not follow the request: {seen}", file=sys.stderr)
        return 1
    if alice[0]["password"] != "secret" or bob[0]["password"] != "bobpass":
        print(f"a password of another user was used: {seen}", file=sys.stderr)
        return 1
    if not unit_settings():
        return 1
    print(f"clickhouse-mcp: ok, 401 without a token, {len(names)} tools, per-request credentials, readonly=1 and limits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
