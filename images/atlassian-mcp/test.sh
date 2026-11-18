#!/usr/bin/env bash
# A test of the image atlassian-mcp (FTR.INFRA.MCPS-0001 R2, R3, R11): not root, a read-only root
# filesystem, 401 without Atlassian credentials, a request with a user token reaches the transport,
# and the list of tools holds no write tool of Jira or Confluence.
set -euo pipefail
image=${1:?the image}
docker run --rm --read-only --tmpfs /tmp \
  -e JIRA_URL=https://jira.invalid -e CONFLUENCE_URL=https://confluence.invalid \
  -e ATLASSIAN_OAUTH_ENABLE=true -e READ_ONLY_MODE=true -e MCP_ALLOWED_URL_DOMAINS=jira.invalid,confluence.invalid \
  --entrypoint python "$image" -c '
import json, os, subprocess, sys, time, urllib.error, urllib.request
assert os.getuid() != 0, "runs as root"
p = subprocess.Popen(["mcp-atlassian", "--transport", "streamable-http", "--stateless", "--host", "127.0.0.1",
                      "--port", "8000", "--path", "/mcp", "--read-only"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
HEAD = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
def post(body, extra=None, session=None):
    head = {**HEAD, **(extra or {})}
    if session:
        head["Mcp-Session-Id"] = session
    return urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:8000/mcp", json.dumps(body).encode(), head), timeout=20)
def init(extra=None):
    try:
        return post(INIT, extra).status
    except urllib.error.HTTPError as error:
        return error.code
for _ in range(120):
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2)
        break
    except Exception:
        time.sleep(0.5)
else:
    p.terminate()
    sys.exit("no healthz: " + p.communicate(timeout=10)[0][-2000:])
if init() != 401:
    p.terminate()
    sys.exit("expected 401 without credentials")
answer = post(INIT, {"Authorization": "Bearer no-such-token"})
session = answer.headers.get("Mcp-Session-Id")
answer.read()
if session:
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, {"Authorization": "Bearer no-such-token"}, session).read()
listed = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, {"Authorization": "Bearer no-such-token"}, session)
raw = listed.read().decode()
line = next((part for part in raw.split("\n") if part.startswith("data:")), raw)
names = [tool["name"] for tool in json.loads(line.removeprefix("data:").strip()).get("result", {}).get("tools", [])]
p.terminate()
out = p.communicate(timeout=15)[0]
if not names:
    sys.exit(f"tools/list is empty\n{out[-2000:]}")
write = [name for name in names if not (name.startswith("jira_get") or name.startswith("jira_search")
         or name.startswith("jira_download") or name.startswith("confluence_get") or name.startswith("confluence_search")
         or name.startswith("confluence_list") or name.startswith("confluence_download") or name.startswith("confluence_check"))]
if write:
    sys.exit(f"not a read-only tool: {write}")
print(f"atlassian-mcp: ok, 401 without credentials, {len(names)} read-only tools")
'
