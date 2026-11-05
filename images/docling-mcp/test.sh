#!/usr/bin/env bash
# A test of the image docling-mcp (FTR.NAB.CMN-0006 IMG-01, IMG-03):
#   - the patch ocr_lang is in place, the process is not root, the root filesystem is read-only;
#   - the server gives the tools of conversion;
#   - the sidecar nginx with the template of the chart (charts/mcp-servers/files/auth.conf.template):
#     401 without a key, and with a key the request reaches the server.
set -euo pipefail
image=${1:?the image}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
set -a
# shellcheck source=../../config/versions.env
source "$ROOT/config/versions.env"
set +a

docker run --rm --read-only --tmpfs /tmp -e HOME=/tmp -e DOCLING_MCP_SERVICE_URL=http://127.0.0.1:9 \
  -e DOCLING_MCP_OCR_LANG=ru,en --entrypoint python "$image" -c '
import inspect, json, os, subprocess, sys, time, urllib.request
import docling_mcp.tools.converters.remote as remote
assert "DOCLING_MCP_OCR_LANG" in inspect.getsource(remote), "no ocr_lang patch"
assert os.getuid() != 0, "runs as root"
p = subprocess.Popen(["docling-mcp-server", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", "8000",
                      "conversion", "generation", "manipulation"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def rpc(body, sid=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
    if sid: h["Mcp-Session-Id"] = sid
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/mcp", json.dumps(body).encode(), h), timeout=30)
    text = r.read().decode()
    data = [l[5:].strip() for l in text.splitlines() if l.startswith("data:")] or [text]
    return r.headers.get("Mcp-Session-Id"), (json.loads(data[-1]) if data[-1] else None)
for _ in range(60):
    try:
        sid, _ = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit("server did not start")
rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
_, res = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
names = {t["name"] for t in res["result"]["tools"]}
for need in ("convert_document_into_docling_document", "export_docling_document_to_markdown", "get_overview_of_document_anchors"):
    assert need in names, need
p.terminate()
print("docling-mcp: ok,", len(names), "tools")
'

# the sidecar: docling-mcp and nginx in one network, as containers of one pod
key=$(openssl rand -hex 16)
name=docling-mcp-test-$$
trap 'docker rm -f "$name" "$name-auth" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$name" -e HOME=/tmp -e DOCLING_MCP_SERVICE_URL=http://127.0.0.1:9 "$image" \
  --transport streamable-http --host 127.0.0.1 --port 8000 conversion >/dev/null
# the key is in the environment of the container (-e without a value takes it from the environment of the script), not in argv
MCP_KEY=$key docker run -d --name "$name-auth" --network "container:$name" -e MCP_KEY -e 'NGINX_ENVSUBST_FILTER=^MCP_KEY$' \
  -v "$ROOT/charts/mcp-servers/files/auth.conf.template:/etc/nginx/templates/default.conf.template:ro" \
  "$MCP_PROXY_IMAGE" >/dev/null
init='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
code() {
  docker run --rm --network "container:$name" -i "$PROBE_CURL_IMAGE" -sS -K - -o /dev/null -w '%{http_code}' \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$init" http://127.0.0.1:8080/mcp
}
for _ in $(seq 1 60); do
  [[ $(printf '' | code 2>/dev/null) == 401 ]] && break
  sleep 1
done
without=$(printf '' | code)
with=$(printf 'header = "Authorization: Bearer %s"\n' "$key" | code)
[[ $without == 401 ]] || { echo "401 was expected without a key, the answer is $without"; exit 1; }
[[ $with == 200 ]] || { echo "200 was expected with a key, the answer is $with"; exit 1; }
echo "docling-mcp: the sidecar gives 401 without a key and 200 with a key"
