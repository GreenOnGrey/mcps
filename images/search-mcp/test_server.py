"""A test of the image search-mcp (FTR.INFRA.MCPS-0003 qa, SRCH-06…SRCH-16).

The MCP client speaks to our server as the proxy of Nabu does. The vendor is a fake DeepSeek on
loopback: it answers with fixtures and with the codes 400, 401, 402, 404, 429, 500, so no test
touches the internet or costs a request. What only the live vendor can show — the quality of
results, a private address, an empty balance — is checked by hand (M-level, SRCH-04, SRCH-14).
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "http://127.0.0.1:8000"
PLATFORM_KEY = "platform-key"
VENDOR_KEY = "vendor-key"
LIMIT = 1024

STATE = {"calls": [], "flaky": 0}


class Vendor(BaseHTTPRequestHandler):
    """The fake DeepSeek: the branch is chosen by the query or the address."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/last":
            self._send(200, STATE["calls"][-1] if STATE["calls"] else {})
            return
        self._send(404, {"error": {"message": "no such path"}})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        STATE["calls"].append({"path": self.path, "body": body,
                               "authorization": self.headers.get("Authorization", "")})
        query = str(body.get("query", ""))
        if self.path.endswith("/v1/tools/search"):
            if query == "bad":
                return self._send(400, {"error": {"message": "'query' must be at most 128 characters"}})
            if query == "key":
                return self._send(401, {"error": {"message": "Invalid token."}})
            if query == "balance":
                return self._send(402, {"error": {"message": "Insufficient balance"}})
            if query == "limit":
                return self._send(429, {"error": {"message": "rate limit"}})
            if query == "flaky":
                STATE["flaky"] += 1
                if STATE["flaky"] <= 2:
                    return self._send(500, {"error": {"message": "Internal Server Error"}})
            if query == "empty":
                return self._send(200, {"results": []})
            content = "x" * (2 * LIMIT) if query == "bigcontent" else ""
            return self._send(200, {"results": [
                {"title": "Kubernetes Documentation", "url": "https://kubernetes.io/docs/",
                 "snippet": "Kubernetes, also known as K8s", "summary": "Long summary for a model",
                 "content": content, "rerank_score": 2.1, "authority": 3,
                 "date": "2026-10-20T08:00:00Z", "site_name": "Kubernetes"},
                {"title": "No date", "url": "https://example.com/b", "snippet": "s", "summary": "m",
                 "content": "", "rerank_score": 1.0, "authority": -1, "date": None, "site_name": None},
            ]})
        if self.path.endswith("/v1/tools/fetch"):
            url = str(body.get("url", ""))
            if url.endswith("/missing"):
                return self._send(404, {"error": {"message": "failed to fetch URL"}})
            content = "y" * (2 * LIMIT) if "big" in url else "# Page\n\nText"
            return self._send(200, {"title": "Page", "content": content})
        self._send(404, {"error": {"message": "no such path"}})


def vendor() -> tuple[ThreadingHTTPServer, int]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Vendor)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def rpc(headers, message, session=None):
    data = json.dumps(message).encode()
    head = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    if session:
        head["Mcp-Session-Id"] = session
    return urllib.request.urlopen(urllib.request.Request(BASE + "/mcp", data, head), timeout=30)


def status(headers) -> int:
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


def connect() -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {PLATFORM_KEY}"}
    answer = rpc(headers, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    session = answer.headers.get("Mcp-Session-Id")
    answer.read()
    if session:
        rpc(headers, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session).read()
    return session or "", headers


def call(session, headers, tool, arguments) -> dict:
    return body(rpc(headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": tool, "arguments": arguments}}, session))


def text_of(answer: dict) -> str:
    result = answer.get("result") or {}
    return "\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")


def is_error(answer: dict) -> bool:
    return bool((answer.get("result") or {}).get("isError"))


def payload(answer: dict) -> dict:
    return json.loads(text_of(answer))


def last_call() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:%d/last" % STATE["port"], timeout=10) as response:
        return json.loads(response.read())


def main() -> int:
    assert os.getuid() != 0, "runs as root"
    httpd, port = vendor()
    STATE["port"] = port
    os.environ.update({"SEARCH_KEY": PLATFORM_KEY, "DEEPSEEK_KEY": VENDOR_KEY, "SEARCH_URL": f"http://127.0.0.1:{port}",
                       "SEARCH_MAX_CONTENT_BYTES": str(LIMIT), "SEARCH_RETRY_DELAY": "0",
                       "SEARCH_RETRIES": "2", "PORT": "8000", "HOST": "127.0.0.1"})
    sys.path.insert(0, os.getcwd())
    from search_mcp import config, deepseek  # noqa: E402  (after the environment is set)

    failures = []

    def expect(condition, message):
        if not condition:
            failures.append(message)

    # A key that is missing is a refusal of the tool, not a crash (SRCH-16).
    try:
        deepseek.search(config.Config(PLATFORM_KEY, "", "http://127.0.0.1:9", 1, LIMIT, 1, 0), "kubernetes", 10, False)
        expect(False, "a call without a key was not refused")
    except deepseek.Refusal as error:
        expect("MCP_SEARCH_KEY" in str(error), "the refusal does not name the key")
    try:
        deepseek.fetch(config.Config(PLATFORM_KEY, VENDOR_KEY, "http://evil.example", 1, LIMIT, 1, 0), "https://example.com")
        expect(False, "a plain http address of the vendor was accepted")
    except deepseek.Refusal as error:
        expect("https" in str(error), "the refusal does not name the scheme")

    server = subprocess.Popen([sys.executable, "-m", "search_mcp.server"],
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

    # Probes stay open, everything else needs the key (SRCH-06, SRCH-16).
    expect(urllib.request.urlopen(BASE + "/health", timeout=10).status == 200, "/health did not answer")
    expect(urllib.request.urlopen(BASE + "/metrics", timeout=10).status == 200, "/metrics did not answer")
    expect(status({}) == 401, "expected 401 without a key")
    expect(status({"Authorization": f"Bearer {VENDOR_KEY}"}) == 401, "the DeepSeek key was accepted as the platform key")
    expect(status({"Authorization": "Bearer wrong"}) == 401, "expected 401 for a wrong key")

    session, headers = connect()
    listed = body(rpc(headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session))
    names = sorted(tool["name"] for tool in listed["result"]["tools"])
    expect(names == ["web_fetch", "web_search"], f"unexpected tools: {names}")
    # The catalog of Nabu shows «изменяет данные» for a tool without readOnlyHint (SRCH-06).
    for tool in listed["result"]["tools"]:
        expect((tool.get("annotations") or {}).get("readOnlyHint") is True,
               f"{tool['name']} is not marked read-only")

    # Search: the default count, no model, no key in the answer (SRCH-07, SRCH-09).
    answer = call(session, headers, "web_search", {"query": "kubernetes"})
    expect(not is_error(answer), f"a search failed: {text_of(answer)}")
    found = payload(answer)
    expect(found["count"] == 2 and found["results"][0]["summary"], f"unexpected results: {found}")
    expect(found["results"][1]["date"] is None, "a null date broke the answer")
    sent = last_call()
    expect(sent["body"].get("count") == 10, f"the default count is not 10: {sent['body']}")
    expect("model" not in sent["body"], "the request carries a model")
    expect(sent["authorization"] == f"Bearer {VENDOR_KEY}", "the vendor did not receive the DeepSeek key")

    # Arguments reach the vendor as they are (SRCH-07, SRCH-08).
    call(session, headers, "web_search", {"query": "kubernetes site:kubernetes.io", "count": 5, "need_content": True})
    sent = last_call()["body"]
    expect(sent["count"] == 5 and sent["need_content"] is True, f"arguments were lost: {sent}")

    # Limits are checked before the vendor is called (SRCH-11, SRCH-12).
    before = len(STATE["calls"])
    for arguments, needle in (({"query": "x" * 129}, "too long"), ({"query": "  "}, "empty"),
                              ({"query": "kubernetes", "count": 0}, "between 1 and 50"),
                              ({"query": "kubernetes", "count": 51}, "between 1 and 50")):
        answer = call(session, headers, "web_search", arguments)
        expect(is_error(answer) and needle in text_of(answer), f"refusal without «{needle}»: {text_of(answer)}")
    expect(len(STATE["calls"]) == before, "a refused query reached the vendor")

    # An empty answer is a success with a zero count, not an error (SRCH-03).
    answer = call(session, headers, "web_search", {"query": "empty"})
    expect(not is_error(answer) and payload(answer)["count"] == 0, "an empty answer was read as an error")

    # The body is cut and marked (SRCH-08).
    answer = call(session, headers, "web_search", {"query": "bigcontent", "need_content": True})
    cut = payload(answer)
    expect(cut["truncated"] and len(cut["results"][0]["content"].encode()) <= LIMIT, "the content was not cut")

    # The refusals of the vendor become the text of the tool, without the key (SRCH-10).
    for query, needle in (("key", "not accepted"), ("balance", "balance"), ("bad", "rejected")):
        answer = call(session, headers, "web_search", {"query": query})
        text = text_of(answer)
        expect(is_error(answer) and needle in text, f"refusal «{needle}»: {text}")
        expect(VENDOR_KEY not in text and PLATFORM_KEY not in text and "Bearer" not in text, "a key leaked into the answer")

    # Retries: an exhausted limit gives up, a flaky vendor recovers (SRCH-10).
    before = len(STATE["calls"])
    answer = call(session, headers, "web_search", {"query": "limit"})
    expect(is_error(answer) and "rate limit" in text_of(answer), f"429 was not reported: {text_of(answer)}")
    expect(len(STATE["calls"]) - before == 3, "429 was not retried twice")
    answer = call(session, headers, "web_search", {"query": "flaky"})
    expect(not is_error(answer), f"a flaky vendor was not retried: {text_of(answer)}")

    # Fetch: Markdown, a cut body, a refusal of the address (SRCH-07, SRCH-08, SRCH-14).
    answer = call(session, headers, "web_fetch", {"url": "https://example.com/page"})
    page = payload(answer)
    expect(page["title"] == "Page" and page["content"].startswith("# Page"), f"unexpected page: {page}")
    answer = call(session, headers, "web_fetch", {"url": "https://example.com/big"})
    expect(payload(answer)["truncated"], "the page body was not cut")
    answer = call(session, headers, "web_fetch", {"url": "http://user:pass@example.com/"})
    expect(is_error(answer), "an address with a login was accepted")
    answer = call(session, headers, "web_fetch", {"url": "https://example.com/missing"})
    expect(is_error(answer) and "unavailable" in text_of(answer), f"404 was not reported: {text_of(answer)}")

    # The metric of the calls and the cut bodies is served (SRCH-16).
    metrics = urllib.request.urlopen(BASE + "/metrics", timeout=10).read().decode()
    expect("search_requests_total" in metrics and "search_request_seconds" in metrics, "metrics are missing")

    server.terminate()
    server.communicate(timeout=15)
    httpd.shutdown()
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("search-mcp: ok, 401 without a key, 2 tools, limits before the vendor, refusals by text, "
          "bodies cut and marked, retries on 429/5xx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
