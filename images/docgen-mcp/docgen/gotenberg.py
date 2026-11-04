"""Клиент doc-convert (FTR.NAB.CHAT-0003 tech §3): Gotenberg с LibreOffice и
Chromium и сайдкар legacy (DOC/XLS/PPT → OOXML)."""

from __future__ import annotations

import base64
import os
import urllib.error
import urllib.request
import uuid

from . import limits
from .errors import DocError


def _auth() -> dict:
    token = base64.b64encode(f"{limits.CONVERT_USER}:{limits.CONVERT_PASSWORD}".encode()).decode()
    return {"Authorization": "Basic " + token}


def multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    """Тело multipart/form-data: поля и файлы (имя поля, имя файла, содержимое)."""
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for field, name, data in files:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _post(url: str, body: bytes, ctype: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=body, method="POST", headers={**_auth(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(limits.MAX_RESULT + 1)
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise DocError("The converter is busy; try again later.") from None
        detail = e.read(300).decode(errors="replace").strip()
        raise DocError(f"The conversion failed: HTTP {e.code} {detail}") from None
    except urllib.error.URLError as e:
        raise DocError(f"The converter is not available: {e.reason}") from None


def office_to_pdf(path: str, pages: str = "") -> bytes:
    """Офисный документ → PDF через LibreOffice."""
    with open(path, "rb") as f:
        data = f.read()
    fields = {"nativePageRanges": pages} if pages else {}
    body, ctype = multipart(fields, [("files", os.path.basename(path), data)])
    return _post(limits.CONVERT_URL + "/forms/libreoffice/convert", body, ctype, limits.TIMEOUT)


def html_to_pdf(index: bytes, assets: list[tuple[str, bytes]], options: dict) -> bytes:
    """HTML → PDF через Chromium без сети: index.html и файлы рядом с ним."""
    fields = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in options.items()}
    files = [("files", "index.html", index)] + [("files", name, data) for name, data in assets]
    body, ctype = multipart(fields, files)
    return _post(limits.CONVERT_URL + "/forms/chromium/convert/html", body, ctype, limits.TIMEOUT)


def legacy(path: str, to: str) -> bytes:
    """DOC/XLS/PPT/ODS… → docx/xlsx/pptx через сайдкар legacy."""
    with open(path, "rb") as f:
        data = f.read()
    name = urllib.request.quote(os.path.basename(path))
    req = urllib.request.Request(f"{limits.LEGACY_URL}/convert?to={to}&name={name}", data=data, method="POST",
                                 headers={**_auth(), "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=limits.TIMEOUT) as resp:
            return resp.read(limits.MAX_RESULT + 1)
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise DocError("The converter is busy; try again later.") from None
        raise DocError(f"The conversion failed: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise DocError(f"The converter is not available: {e.reason}") from None
