#!/usr/bin/env bash
# A test of the image docgen-mcp together with doc-convert (FTR.NAB.CHAT-0003 qa, level C):
# a Docker network with no way out; "S3" is an HTTP server with GET and PUT, "sink" is
# a server that counts requests to itself (a check of SSRF). Checks: 401 without
# a key, six tools, DOCX and PDF from Markdown, DOCX → PDF, HTML with
# external links → PDF without a single request to sink, not root, a read-only
# root.
#   images/docgen-mcp/test.sh <image docgen-mcp> [image doc-convert]
set -euo pipefail
image=${1:?the image docgen-mcp}
convert=${2:-${DOC_CONVERT_IMAGE:-}}
[[ -n $convert ]] || { echo "the image doc-convert is required (the second argument or DOC_CONVERT_IMAGE)" >&2; exit 2; }
net=docgen-test-$$
cleanup() { docker rm -f "$net-s3" "$net-sink" "$net-convert" "$net-legacy" "$net-docgen" >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create --internal "$net" >/dev/null

# "S3" and sink are the same image docgen with a small server in python
server='
import http.server, os, sys
root = "/tmp/s3"; os.makedirs(root, exist_ok=True)
hits = [0]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/__hits":
            body = str(hits[0]).encode(); self.send_response(200); self.end_headers(); self.wfile.write(body); return
        hits[0] += 1
        p = os.path.join(root, self.path.split("?")[0].lstrip("/"))
        if not os.path.isfile(p): self.send_response(404); self.end_headers(); return
        data = open(p, "rb").read(); self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_HEAD = do_GET
    def do_PUT(self):
        hits[0] += 1
        p = os.path.join(root, self.path.split("?")[0].lstrip("/")); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(self.rfile.read(int(self.headers["Content-Length"]))); self.send_response(200); self.end_headers()
    do_POST = do_PUT
http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
'
docker run -d --name "$net-s3" --network "$net" --network-alias s3 --entrypoint python "$image" -c "$server" 9000 >/dev/null
docker run -d --name "$net-sink" --network "$net" --network-alias sink --entrypoint python "$image" -c "$server" 80 >/dev/null
docker run -d --name "$net-convert" --network "$net" --network-alias doc-convert --read-only --tmpfs /tmp --tmpfs /home/gotenberg \
  -e GOTENBERG_API_BASIC_AUTH_USERNAME=u -e GOTENBERG_API_BASIC_AUTH_PASSWORD=p "$convert" \
  gotenberg --api-enable-basic-auth --libreoffice-restart-after=1 --chromium-restart-after=1 \
  --chromium-allow-list='^file:///tmp/.*' --chromium-deny-list='^(?!file:///tmp/).*' --chromium-deny-private-ips --chromium-deny-public-ips >/dev/null
docker run -d --name "$net-legacy" --network "$net" --network-alias doc-convert-legacy --read-only --tmpfs /tmp -e HOME=/tmp \
  -e GOTENBERG_API_BASIC_AUTH_USERNAME=u -e GOTENBERG_API_BASIC_AUTH_PASSWORD=p "$convert" python3 /opt/doc-convert/legacy.py >/dev/null
docker run -d --name "$net-docgen" --network "$net" --read-only --tmpfs /tmp \
  -e DOCGEN_KEY=k -e DOCGEN_S3_HOSTS=s3:9000 -e DOC_CONVERT_URL=http://doc-convert:3000 -e DOC_CONVERT_LEGACY_URL=http://doc-convert-legacy:3001 \
  -e DOC_CONVERT_USER=u -e DOC_CONVERT_PASSWORD=p "$image" >/dev/null

docker run --rm --network "$net" --entrypoint python "$image" -c '
import base64, io, json, sys, time, urllib.error, urllib.request, zipfile
D = "http://'"$net"'-docgen:8000"
def wait(u):
    for _ in range(120):
        try:
            urllib.request.urlopen(u, timeout=2); return
        except Exception:
            time.sleep(0.5)
    sys.exit("not up: " + u)
wait(D + "/healthz"); wait("http://s3:9000/__hits"); wait("http://sink/__hits")
for _ in range(120):
    try:
        urllib.request.urlopen(urllib.request.Request("http://doc-convert:3000/health"), timeout=2); break
    except urllib.error.HTTPError: break
    except Exception: time.sleep(1)
def put(name, data):
    urllib.request.urlopen(urllib.request.Request("http://s3:9000/" + name, data=data, method="PUT", headers={"Content-Length": str(len(data))}))
def get(name):
    return urllib.request.urlopen("http://s3:9000/" + name).read()
def rpc(method, params=None, key="k"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key: h["Authorization"] = "Bearer " + key
    try:
        with urllib.request.urlopen(urllib.request.Request(D + "/mcp", body, h), timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
def call(name, args):
    s, out = rpc("tools/call", {"name": name, "arguments": args})
    res = out["result"]
    if res.get("isError"): sys.exit(f"{name}: {res}")
    return json.loads(res["content"][0]["text"])
assert rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, key="")[0] == 401, "SEC-12"
assert rpc("tools/list", key="bad")[0] == 401, "SEC-12"
s, out = rpc("tools/list")
names = sorted(t["name"] for t in out["result"]["tools"])
assert names == ["convert", "edit_document", "fill_template", "pdf_tools", "render_document", "render_spreadsheet"], names
md = "---\ntitle: Итоги\n---\n# Раздел\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
r = call("render_document", {"markdown": md, "format": "docx", "dest": "http://s3:9000/out/a.docx"})
assert zipfile.ZipFile(io.BytesIO(get("out/a.docx"))).read("word/document.xml"), r   # GEN-01
r = call("render_document", {"markdown": md, "format": "pdf", "dest": "http://s3:9000/out/a.pdf"})
assert get("out/a.pdf")[:5] == b"%PDF-" and r["pages"] >= 1, r                     # GEN-02
put("in/offer.docx", get("out/a.docx"))
r = call("convert", {"source": "http://s3:9000/in/offer.docx#nabu-path=docs/offer.docx", "to": "pdf", "dest": "http://s3:9000/out/offer.pdf"})
assert get("out/offer.pdf")[:5] == b"%PDF-", r                                       # CNV-01
html = b"""<html><body><h1>Report</h1><canvas id=c></canvas>
<img src="http://sink/img.png"><iframe src="http://sink/frame"></iframe><link rel=stylesheet href="http://sink/a.css">
<style>@import url(http://sink/b.css);</style><img src="file:///etc/passwd">
<script>fetch("http://sink/fetch").catch(()=>{}); document.body.append("JS ran"); window.status="ready";</script></body></html>"""
put("in/report.html", html)
before = int(urllib.request.urlopen("http://sink/__hits").read())
r = call("convert", {"source": "http://s3:9000/in/report.html", "to": "pdf", "dest": "http://s3:9000/out/report.pdf"})
after = int(urllib.request.urlopen("http://sink/__hits").read())
assert get("out/report.pdf")[:5] == b"%PDF-", r                                      # CNV-03
assert after == before, f"SEC-01: the sink got {after - before} requests"
assert any("http://sink/" in w for w in r.get("warnings", [])), r
print("docgen-mcp: ok")
'
docker exec "$net-docgen" python -c 'import os; assert os.getuid() != 0, "IMG-02: root"; import sys; sys.exit(0)'
echo "docgen-mcp + doc-convert: ok"
