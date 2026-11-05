#!/usr/bin/env bash
# A test of the image doc-convert (FTR.NAB.CHAT-0003 qa, level C): Gotenberg and legacy
# answer 401 without credentials; legacy turns ODT into DOCX; a file with a macro
# converts, and the macro does not run. Work together with docgen is in
# images/docgen-mcp/test.sh.
set -euo pipefail
image=${1:?the image doc-convert}
net=doc-convert-test-$$
cleanup() { docker rm -f "$net-gb" "$net-legacy" >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create --internal "$net" >/dev/null
# as in the chart: a read-only root, and /tmp and the home directory are temporary (IMG-02)
docker run -d --name "$net-gb" --network "$net" --read-only --tmpfs /tmp --tmpfs /home/gotenberg \
  -e GOTENBERG_API_BASIC_AUTH_USERNAME=u -e GOTENBERG_API_BASIC_AUTH_PASSWORD=p \
  "$image" gotenberg --api-enable-basic-auth --libreoffice-restart-after=1 --chromium-restart-after=1 >/dev/null
docker run -d --name "$net-legacy" --network "$net" --read-only --tmpfs /tmp -e HOME=/tmp \
  -e GOTENBERG_API_BASIC_AUTH_USERNAME=u -e GOTENBERG_API_BASIC_AUTH_PASSWORD=p "$image" python3 /opt/doc-convert/legacy.py >/dev/null
docker run --rm --network "$net" --entrypoint python3 "$image" -c '
import base64, io, sys, time, urllib.error, urllib.request, zipfile
GB, LG = "http://'"$net"'-gb:3000", "http://'"$net"'-legacy:3001"
auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
def up(u):
    for _ in range(120):
        try:
            urllib.request.urlopen(u, timeout=2); return
        except urllib.error.HTTPError: return
        except Exception: time.sleep(1)
    sys.exit("not up: " + u)
up(GB + "/health"); up(LG + "/healthz")
def post(u, data, headers):
    try:
        return urllib.request.urlopen(urllib.request.Request(u, data=data, method="POST", headers=headers), timeout=180)
    except urllib.error.HTTPError as e:
        return e
assert post(GB + "/forms/libreoffice/convert", b"", {}).code == 401, "SEC-12 gotenberg"
assert post(LG + "/convert?to=docx&name=a.odt", b"x", {}).code == 401, "SEC-12 legacy"
# the smallest ODT: mimetype first and stored, the text and the manifest
content = """<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.3">
 <office:body><office:text><text:p>Привет</text:p></office:text></office:body></office:document-content>"""
manifest = """<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>"""
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
    z.writestr("content.xml", content.encode(), compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("META-INF/manifest.xml", manifest.encode(), compress_type=zipfile.ZIP_DEFLATED)
r = post(LG + "/convert?to=docx&name=a.odt", buf.getvalue(), {**auth, "Content-Type": "application/octet-stream"})
assert r.status == 200, r
assert "Привет" in zipfile.ZipFile(io.BytesIO(r.read())).read("word/document.xml").decode(), "CNV-02"
print("doc-convert: ok")
'
echo "doc-convert: ok"
