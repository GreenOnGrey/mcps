"""legacy — DOC/XLS/PPT/ODF/RTF → DOCX/XLSX/PPTX на LibreOffice (FTR.NAB.CHAT-0003
tech §3.3). Gotenberg выдаёт только PDF, поэтому эти преобразования делает
этот сайдкар на том же образе: на каждый запрос — новый профиль LibreOffice
(макросы выключены, внешние ссылки не обновляются) и временный каталог,
одна конвертация за раз, очередь — до пяти запросов.

POST /convert?to=docx|xlsx|pptx&name=<имя файла>, тело — файл; ответ — файл.
"""

from __future__ import annotations

import base64
import hmac
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.parse

PORT = int(os.environ.get("LEGACY_PORT", "3001"))
USER = os.environ.get("GOTENBERG_API_BASIC_AUTH_USERNAME", "")
PASSWORD = os.environ.get("GOTENBERG_API_BASIC_AUTH_PASSWORD", "")
TIMEOUT = int(os.environ.get("LEGACY_TIMEOUT", "120"))
MAX_BYTES = int(os.environ.get("LEGACY_MAX_MB", "50")) << 20
PROFILE = os.path.join(os.path.dirname(__file__), "registrymodifications.xcu")
SOURCES = {"doc", "xls", "ppt", "odt", "ods", "odp", "rtf"}
TARGETS = {"docx": "docx:MS Word 2007 XML", "xlsx": "xlsx:Calc MS Excel 2007 XML", "pptx": "pptx:Impress MS PowerPoint 2007 XML"}

slot = threading.Semaphore(1)
queue = threading.Semaphore(6)  # одна в работе и пять в очереди


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # без имён файлов и содержимого
        pass

    def _send(self, status: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._send(200 if self.path == "/healthz" else 404, b"ok\n" if self.path == "/healthz" else b"")

    def _authorized(self) -> bool:
        want = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        got = self.headers.get("Authorization", "")
        return bool(USER) and hmac.compare_digest(got.encode(), f"Basic {want}".encode())

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return self._send(401, b"unauthorized\n")
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        to = (q.get("to") or [""])[0]
        name = os.path.basename((q.get("name") or ["file"])[0]) or "file"
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if u.path != "/convert" or to not in TARGETS or ext not in SOURCES:
            return self._send(400, b"POST /convert?to=docx|xlsx|pptx&name=<.doc|.xls|.ppt|.odt|.ods|.odp|.rtf>\n")
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > MAX_BYTES:
            return self._send(413, b"too large\n")
        data = self.rfile.read(n)
        if not queue.acquire(blocking=False):
            return self._send(503, b"busy\n")
        try:
            with slot:
                out = convert(name, ext, to, data)
        except TimeoutError:
            return self._send(504, b"timeout\n")
        except RuntimeError as e:
            return self._send(422, str(e).encode())
        finally:
            queue.release()
        self._send(200, out, "application/octet-stream")


def convert(name: str, ext: str, to: str, data: bytes) -> bytes:
    work = tempfile.mkdtemp(prefix="legacy-")
    try:
        profile = os.path.join(work, "profile", "user")
        os.makedirs(profile)
        shutil.copy(PROFILE, os.path.join(profile, "registrymodifications.xcu"))
        src = os.path.join(work, "in." + ext)
        with open(src, "wb") as f:
            f.write(data)
        out_dir = os.path.join(work, "out")
        try:
            subprocess.run(["soffice", "--headless", "--norestore", "--nologo", "--nolockcheck",
                            f"-env:UserInstallation=file://{os.path.join(work, 'profile')}",
                            "--convert-to", TARGETS[to], "--outdir", out_dir, src],
                           capture_output=True, timeout=TIMEOUT, check=False, env={**os.environ, "HOME": work})
        except subprocess.TimeoutExpired:
            raise TimeoutError from None
        result = os.path.join(out_dir, "in." + to)
        if not os.path.isfile(result):
            raise RuntimeError("the file could not be converted\n")
        with open(result, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
