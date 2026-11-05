#!/usr/bin/env bash
# Тест образа docling-mcp (FTR.NAB.CMN-0006 IMG-01, IMG-03):
#   - правка ocr_lang на месте, процесс не от root, корневая ФС только для чтения;
#   - сервер отдаёт инструменты конвертации;
#   - сайдкар nginx с шаблоном чарта (charts/mcp-servers/files/auth.conf.template):
#     без ключа — 401, с ключом — запрос доходит до сервера.
set -euo pipefail
image=${1:?образ}
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

# сайдкар: docling-mcp и nginx в одной сети, как контейнеры одного пода
key=$(openssl rand -hex 16)
name=docling-mcp-test-$$
trap 'docker rm -f "$name" "$name-auth" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$name" -e HOME=/tmp -e DOCLING_MCP_SERVICE_URL=http://127.0.0.1:9 "$image" \
  --transport streamable-http --host 127.0.0.1 --port 8000 conversion >/dev/null
# ключ — в окружении контейнера (-e без значения берёт его из окружения скрипта), не в argv
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
[[ $without == 401 ]] || { echo "без ключа ожидался 401, ответ $without"; exit 1; }
[[ $with == 200 ]] || { echo "с ключом ожидался 200, ответ $with"; exit 1; }
echo "docling-mcp: сайдкар — без ключа 401, с ключом 200"
